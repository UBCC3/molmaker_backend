import os
from datetime import timezone
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest
from psycopg2 import DatabaseError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from conftest import TestingSessionLocal, engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "001_pr14_database_changes.sql"
ORCHESTRATION_MIGRATION_PATH = (
    PROJECT_ROOT / "migrations" / "002_jobs_orchestration_redesign.sql"
)
LEGACY_SCHEMA_PATH = PROJECT_ROOT / "tests" / "fixtures" / "pre_pr14_schema.sql"
DUMP_PATH = PROJECT_ROOT / "molmaker.sql"

GROUP_ID = "00000000-0000-0000-0000-000000000001"
OLD_JOB_ID = "10000000-0000-0000-0000-000000000001"
NEW_JOB_ID = "10000000-0000-0000-0000-000000000002"
PENDING_JOB_ID = "10000000-0000-0000-0000-000000000003"
ACCEPTED_PENDING_JOB_ID = "10000000-0000-0000-0000-000000000004"
OUT_OF_MEMORY_JOB_ID = "10000000-0000-0000-0000-000000000005"
TIMEOUT_JOB_ID = "10000000-0000-0000-0000-000000000006"
OLD_STRUCTURE_ID = "20000000-0000-0000-0000-000000000001"
NEW_STRUCTURE_ID = "20000000-0000-0000-0000-000000000002"
OLDEST_REQUEST_ID = "30000000-0000-0000-0000-000000000001"
DUPLICATE_REQUEST_ID = "30000000-0000-0000-0000-000000000002"
RESOLVED_REQUEST_ID = "30000000-0000-0000-0000-000000000003"
CANONICAL_TAG_ID = "40000000-0000-0000-0000-000000000001"

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="requires PostgreSQL migration and dump support",
)


def _reset_public_schema(db):
    db.close()
    engine.dispose()
    connection = engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cursor.close()
        connection.commit()
    finally:
        connection.close()


def _run_sql_file(path):
    connection = engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(path.read_text())
        cursor.close()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _restore_dump(db):
    _reset_public_schema(db)
    psql = shutil.which("psql")
    assert psql, "The PostgreSQL test job must provide the psql command"

    database_url = engine.url
    command = [psql, "-X", "-v", "ON_ERROR_STOP=1"]
    if database_url.host:
        command.extend(["--host", database_url.host])
    if database_url.port:
        command.extend(["--port", str(database_url.port)])
    if database_url.username:
        command.extend(["--username", database_url.username])
    command.extend(["--dbname", database_url.database, "--file", str(DUMP_PATH)])

    environment = os.environ.copy()
    if database_url.password:
        environment["PGPASSWORD"] = database_url.password

    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _column_names(session, table_name):
    return set(
        session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).scalars()
    )


def _constraint_names(session):
    return set(
        session.execute(
            text(
                """
                SELECT conname
                FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace
                """
            )
        ).scalars()
    )


def _index_names(session):
    return set(
        session.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                """
            )
        ).scalars()
    )


def _database_state():
    session = TestingSessionLocal()
    try:
        tables = session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
        ).scalars().all()
        data = {
            table_name: session.execute(
                text(
                    f"""
                    SELECT to_jsonb(row_data)
                    FROM public.{table_name} AS row_data
                    ORDER BY to_jsonb(row_data)::text
                    """
                )
            ).scalars().all()
            for table_name in tables
        }
        columns = session.execute(
            text(
                """
                SELECT
                    table_name,
                    column_name,
                    data_type,
                    is_nullable,
                    column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
        ).all()
        constraints = session.execute(
            text(
                """
                SELECT conname, pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace
                ORDER BY conname
                """
            )
        ).all()
        indexes = session.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                ORDER BY indexname
                """
            )
        ).all()
        return {
            "data": data,
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
        }
    finally:
        session.close()


def _assert_constraint(session, statement, expected_name, parameters=None):
    with pytest.raises(IntegrityError) as caught:
        session.execute(text(statement), parameters or {})
        session.commit()

    session.rollback()
    assert caught.value.orig.diag.constraint_name == expected_name


def test_combined_migration_upgrades_main_schema(db):
    _reset_public_schema(db)
    _run_sql_file(LEGACY_SCHEMA_PATH)
    _run_sql_file(MIGRATION_PATH)

    session = TestingSessionLocal()
    try:
        assert "group_id" in _column_names(session, "jobs")
        assert {"group_id", "is_public"} <= _column_names(session, "structures")
        assert {
            "request_type",
            "created_by_sub",
            "resolved_by_sub",
            "expires_at",
            "resolved_at",
            "group_name_snapshot",
        } <= _column_names(session, "requests")
        assert "role_or_group_updated_at" in _column_names(session, "users")
        assert "member_since" not in _column_names(session, "users")

        timestamp_column = session.execute(
            text(
                """
                SELECT is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'users'
                  AND column_name = 'role_or_group_updated_at'
                """
            )
        ).one()
        assert timestamp_column.is_nullable == "NO"
        assert timestamp_column.column_default == "now()"

        job_groups = dict(
            session.execute(
                text("SELECT job_id::text, group_id::text FROM public.jobs")
            ).all()
        )
        assert job_groups[OLD_JOB_ID] is None
        assert job_groups[NEW_JOB_ID] == GROUP_ID

        structure_groups = dict(
            session.execute(
                text("SELECT structure_id::text, group_id::text FROM public.structures")
            ).all()
        )
        assert structure_groups[OLD_STRUCTURE_ID] is None
        assert structure_groups[NEW_STRUCTURE_ID] == GROUP_ID

        requests = {
            row.request_id: row
            for row in session.execute(
                text(
                    """
                    SELECT
                        request_id::text AS request_id,
                        status,
                        request_type,
                        requested_at,
                        expires_at,
                        resolved_at,
                        created_by_sub,
                        sender_email_snapshot,
                        receiver_email_snapshot,
                        group_name_snapshot
                    FROM public.requests
                    """
                )
            )
        }
        assert requests[OLDEST_REQUEST_ID].status == "pending"
        assert requests[DUPLICATE_REQUEST_ID].status == "cancelled"
        assert requests[DUPLICATE_REQUEST_ID].resolved_at is not None
        assert requests[RESOLVED_REQUEST_ID].status == "approved"
        assert requests[RESOLVED_REQUEST_ID].requested_at is not None
        assert requests[RESOLVED_REQUEST_ID].resolved_at is not None
        for request in requests.values():
            assert request.request_type == "invite"
            assert request.expires_at is not None
            assert request.created_by_sub == "auth0|owner"
            assert request.sender_email_snapshot == "owner@example.com"
            assert request.receiver_email_snapshot is not None
            assert request.group_name_snapshot == "First group"

        saved_timestamp = session.execute(
            text(
                """
                SELECT role_or_group_updated_at
                FROM public.users
                WHERE user_sub = 'auth0|owner'
                """
            )
        ).scalar_one()
        assert saved_timestamp.astimezone(timezone.utc).isoformat() == (
            "2025-01-10T00:00:00+00:00"
        )
        assert session.execute(
            text(
                """
                SELECT count(*)
                FROM public.users
                WHERE role_or_group_updated_at IS NULL
                """
            )
        ).scalar_one() == 0

        tags = session.execute(
            text("SELECT tag_id::text FROM public.tags")
        ).scalars().all()
        assert tags == [CANONICAL_TAG_ID]
        assert session.execute(
            text("SELECT count(*) FROM public.jobs_tags")
        ).scalar_one() == 1
        assert session.execute(
            text("SELECT count(*) FROM public.structures_tags")
        ).scalar_one() == 1

        assert {
            "ck_jobs_owner_present",
            "ck_structures_owner_present",
            "fk_jobs_group_id",
            "fk_structures_group_id",
            "uq_tags_user_sub_name",
            "requests_created_by_sub_fkey",
            "requests_resolved_by_sub_fkey",
        } <= _constraint_names(session)
        assert {
            "uq_requests_pending_invite",
            "uq_requests_pending_join",
            "uq_requests_pending_demember",
        } <= _index_names(session)
    finally:
        session.close()


def test_combined_migration_can_run_twice(db):
    _reset_public_schema(db)
    _run_sql_file(LEGACY_SCHEMA_PATH)
    _run_sql_file(MIGRATION_PATH)
    state_after_first_run = _database_state()

    _run_sql_file(MIGRATION_PATH)

    assert _database_state() == state_after_first_run


def test_jobs_orchestration_migration_upgrades_current_schema(db):
    _reset_public_schema(db)
    _run_sql_file(LEGACY_SCHEMA_PATH)
    _run_sql_file(MIGRATION_PATH)

    session = TestingSessionLocal()
    try:
        session.execute(
            text(
                """
                INSERT INTO public.jobs (
                    job_id, filename, status, calculation_type, method,
                    basis_set, submitted_at, user_sub, slurm_id, is_deleted,
                    is_public, is_uploaded
                ) VALUES
                    (
                        :pending_id, 'pending.xyz', 'pending', 'energy', 'hf',
                        'sto-3g', NOW(), 'auth0|owner', NULL, false, false,
                        false
                    ),
                    (
                        :accepted_id, 'accepted.xyz', 'pending', 'energy',
                        'hf', 'sto-3g', NOW(), 'auth0|owner', 12345, false,
                        false, false
                    ),
                    (
                        :oom_id, 'oom.xyz', 'out_of_memory', 'energy', 'hf',
                        'sto-3g', NOW(), 'auth0|owner', 12346, false, false,
                        false
                    ),
                    (
                        :timeout_id, 'timeout.xyz', 'timeout', 'energy', 'hf',
                        'sto-3g', NOW(), 'auth0|owner', 12347, false, false,
                        false
                    )
                """
            ),
            {
                "pending_id": PENDING_JOB_ID,
                "accepted_id": ACCEPTED_PENDING_JOB_ID,
                "oom_id": OUT_OF_MEMORY_JOB_ID,
                "timeout_id": TIMEOUT_JOB_ID,
            },
        )
        session.commit()
    finally:
        session.close()

    _run_sql_file(ORCHESTRATION_MIGRATION_PATH)

    session = TestingSessionLocal()
    try:
        job_columns = _column_names(session, "jobs")
        assert {
            "attempt_count",
            "terminal_status",
            "cancel_requested",
            "failure_reason",
            "failure_message",
            "optimization_type",
        } <= job_columns
        assert {
            "retry_count",
            "slurm_state",
            "slurm_exit_code",
            "submission_attempted_at",
            "cancel_requested_at",
            "artifact_manifest",
        }.isdisjoint(job_columns)

        slurm_id_type = session.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'jobs'
                  AND column_name = 'slurm_id'
                """
            )
        ).scalar_one()
        assert slurm_id_type == "character varying"

        migrated_jobs = {
            row.job_id: row
            for row in session.execute(
                text(
                    """
                    SELECT
                        job_id::text AS job_id,
                        status,
                        slurm_id,
                        attempt_count,
                        cancel_requested,
                        failure_reason,
                        completed_at
                    FROM public.jobs
                    WHERE job_id IN (
                        :pending_id,
                        :accepted_id,
                        :oom_id,
                        :timeout_id
                    )
                    """
                ),
                {
                    "pending_id": PENDING_JOB_ID,
                    "accepted_id": ACCEPTED_PENDING_JOB_ID,
                    "oom_id": OUT_OF_MEMORY_JOB_ID,
                    "timeout_id": TIMEOUT_JOB_ID,
                },
            )
        }
        assert migrated_jobs[PENDING_JOB_ID].status == "submitting"
        assert migrated_jobs[ACCEPTED_PENDING_JOB_ID].status == "submitted"
        assert migrated_jobs[ACCEPTED_PENDING_JOB_ID].slurm_id == "12345"
        assert migrated_jobs[OUT_OF_MEMORY_JOB_ID].status == "failed"
        assert (
            migrated_jobs[OUT_OF_MEMORY_JOB_ID].failure_reason
            == "out_of_memory"
        )
        assert migrated_jobs[OUT_OF_MEMORY_JOB_ID].completed_at is not None
        assert migrated_jobs[TIMEOUT_JOB_ID].status == "failed"
        assert migrated_jobs[TIMEOUT_JOB_ID].failure_reason == "timeout"
        assert migrated_jobs[TIMEOUT_JOB_ID].completed_at is not None
        assert all(
            row.attempt_count == 0
            and row.cancel_requested is False
            for row in migrated_jobs.values()
        )

        index_definition = session.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'idx_jobs_orchestration_active'
                """
            )
        ).scalar_one()
        assert "(status, submitted_at, job_id)" in index_definition
        assert "WHERE" in index_definition
        assert "submitting" in index_definition
        assert "submitted" in index_definition
        assert "running" in index_definition
        assert "finalising" in index_definition
        assert "is_deleted" not in index_definition
    finally:
        session.close()


def test_jobs_orchestration_migration_can_run_twice(db):
    _reset_public_schema(db)
    _run_sql_file(LEGACY_SCHEMA_PATH)
    _run_sql_file(MIGRATION_PATH)
    _run_sql_file(ORCHESTRATION_MIGRATION_PATH)
    state_after_first_run = _database_state()

    _run_sql_file(ORCHESTRATION_MIGRATION_PATH)

    assert _database_state() == state_after_first_run


def test_jobs_orchestration_migration_adds_retained_job_inputs(db):
    _reset_public_schema(db)
    _run_sql_file(LEGACY_SCHEMA_PATH)
    _run_sql_file(MIGRATION_PATH)
    _run_sql_file(ORCHESTRATION_MIGRATION_PATH)

    session = TestingSessionLocal()
    try:
        columns = {
            row.column_name: row
            for row in session.execute(
                text(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'job_inputs'
                    """
                )
            )
        }
        assert set(columns) == {"job_id", "input_xyz", "keywords"}
        assert columns["job_id"].data_type == "uuid"
        assert columns["job_id"].is_nullable == "NO"
        assert columns["input_xyz"].data_type == "text"
        assert columns["input_xyz"].is_nullable == "NO"
        assert columns["keywords"].data_type == "jsonb"
        assert columns["keywords"].is_nullable == "YES"

        constraints = {
            row.conname: row.definition
            for row in session.execute(
                text(
                    """
                    SELECT
                        conname,
                        pg_get_constraintdef(oid) AS definition
                    FROM pg_constraint
                    WHERE conrelid = 'public.job_inputs'::regclass
                    """
                )
            )
        }
        assert constraints["job_inputs_pkey"] == "PRIMARY KEY (job_id)"
        assert "FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE" in (
            constraints["job_inputs_job_id_fkey"]
        )
    finally:
        session.close()


def test_migration_does_not_restore_removed_group_ownership(db):
    _reset_public_schema(db)
    _run_sql_file(LEGACY_SCHEMA_PATH)
    _run_sql_file(MIGRATION_PATH)

    session = TestingSessionLocal()
    try:
        session.execute(
            text("UPDATE public.jobs SET group_id = NULL WHERE job_id = :job_id"),
            {"job_id": NEW_JOB_ID},
        )
        session.execute(
            text(
                """
                UPDATE public.structures
                SET group_id = NULL
                WHERE structure_id = :structure_id
                """
            ),
            {"structure_id": NEW_STRUCTURE_ID},
        )
        session.commit()
    finally:
        session.close()

    state_before_second_run = _database_state()
    _run_sql_file(MIGRATION_PATH)

    assert _database_state() == state_before_second_run


@pytest.mark.parametrize(
    ("schema_change", "expected_error"),
    [
        (
            """
            ALTER TABLE public.users
            ADD COLUMN role_or_group_updated_at timestamp with time zone
            """,
            "users has both member_since and role_or_group_updated_at",
        ),
        (
            "ALTER TABLE public.users DROP COLUMN member_since",
            "users is missing both member_since and role_or_group_updated_at",
        ),
    ],
)
def test_combined_migration_rejects_unexpected_timestamp_columns(
    db,
    schema_change,
    expected_error,
):
    _reset_public_schema(db)
    _run_sql_file(LEGACY_SCHEMA_PATH)
    session = TestingSessionLocal()
    try:
        session.execute(text(schema_change))
        session.commit()
    finally:
        session.close()

    with pytest.raises(DatabaseError, match=expected_error):
        _run_sql_file(MIGRATION_PATH)


def test_molmaker_dump_restores_schema_and_data(db):
    dump = DUMP_PATH.read_text()
    assert "OWNER TO" not in dump
    assert "\nGRANT " not in dump
    assert "\nREVOKE " not in dump
    assert r"\\N" not in dump

    _restore_dump(db)
    session = TestingSessionLocal()
    try:
        counts = dict(
            session.execute(
                text(
                    """
                    SELECT 'groups', count(*) FROM public.groups
                    UNION ALL SELECT 'users', count(*) FROM public.users
                    UNION ALL SELECT 'jobs', count(*) FROM public.jobs
                    UNION ALL SELECT 'job_inputs', count(*) FROM public.job_inputs
                    UNION ALL SELECT 'structures', count(*) FROM public.structures
                    UNION ALL SELECT 'requests', count(*) FROM public.requests
                    UNION ALL SELECT 'tags', count(*) FROM public.tags
                    """
                )
            ).all()
        )
        assert counts == {
            "groups": 3,
            "users": 6,
            "jobs": 13,
            "job_inputs": 0,
            "structures": 9,
            "requests": 3,
            "tags": 11,
        }

        job_columns = _column_names(session, "jobs")
        assert {
            "group_id",
            "attempt_count",
            "terminal_status",
            "cancel_requested",
            "failure_reason",
            "failure_message",
            "optimization_type",
        } <= job_columns
        assert {
            "retry_count",
            "slurm_state",
            "slurm_exit_code",
            "submission_attempted_at",
            "cancel_requested_at",
            "artifact_manifest",
        }.isdisjoint(job_columns)
        assert _column_names(session, "job_inputs") == {
            "job_id",
            "input_xyz",
            "keywords",
        }
        assert {
            "job_inputs_pkey",
            "job_inputs_job_id_fkey",
        } <= _constraint_names(session)
        assert {"group_id", "is_public"} <= _column_names(session, "structures")
        assert "role_or_group_updated_at" in _column_names(session, "users")
        assert "member_since" not in _column_names(session, "users")
        assert session.execute(
            text("SELECT count(*) FROM public.jobs WHERE group_id IS NOT NULL")
        ).scalar_one() == 4
        assert session.execute(
            text("SELECT count(*) FROM public.structures WHERE group_id IS NOT NULL")
        ).scalar_one() == 1
        assert session.execute(
            text("SELECT count(*) FROM public.users WHERE role_or_group_updated_at IS NULL")
        ).scalar_one() == 0
        assert session.execute(
            text("SELECT count(*) FROM public.requests WHERE resolved_by_sub IS NULL")
        ).scalar_one() == 3
        assert session.execute(
            text(
                """
                SELECT count(*)
                FROM public.jobs
                WHERE attempt_count = 0
                  AND cancel_requested = false
                """
            )
        ).scalar_one() == 13
        assert {
            "uq_requests_pending_invite",
            "uq_requests_pending_join",
            "uq_requests_pending_demember",
            "idx_jobs_orchestration_active",
        } <= _index_names(session)
    finally:
        session.close()


def test_migrations_are_safe_after_restoring_molmaker_dump(db):
    _restore_dump(db)
    state_before_migration = _database_state()

    _run_sql_file(MIGRATION_PATH)
    _run_sql_file(ORCHESTRATION_MIGRATION_PATH)

    assert _database_state() == state_before_migration


def test_restored_dump_enforces_foreign_keys_and_owner_checks(db):
    _restore_dump(db)
    session = TestingSessionLocal()
    try:
        _assert_constraint(
            session,
            """
            INSERT INTO public.jobs (
                job_id, filename, status, calculation_type, method, basis_set,
                submitted_at, user_sub, group_id, is_deleted, is_public,
                is_uploaded
            ) VALUES (
                :row_id, 'invalid.xyz', 'pending', 'energy', 'hf', 'sto-3g',
                NOW(), NULL, NULL, false, false, false
            )
            """,
            "ck_jobs_owner_present",
            {"row_id": uuid.uuid4()},
        )
        _assert_constraint(
            session,
            """
            INSERT INTO public.structures (
                structure_id, user_sub, group_id, name, location, uploaded_at,
                is_deleted, formula, is_public
            ) VALUES (
                :row_id, NULL, NULL, 'Invalid', 's3://invalid', NOW(),
                false, 'H2O', false
            )
            """,
            "ck_structures_owner_present",
            {"row_id": uuid.uuid4()},
        )
        _assert_constraint(
            session,
            """
            INSERT INTO public.jobs (
                job_id, filename, status, calculation_type, method, basis_set,
                submitted_at, user_sub, group_id, is_deleted, is_public,
                is_uploaded
            ) VALUES (
                :row_id, 'invalid-group.xyz', 'pending', 'energy', 'hf',
                'sto-3g', NOW(), NULL, :group_id, false, false, false
            )
            """,
            "fk_jobs_group_id",
            {"row_id": uuid.uuid4(), "group_id": uuid.uuid4()},
        )
        _assert_constraint(
            session,
            """
            INSERT INTO public.structures (
                structure_id, user_sub, group_id, name, location, uploaded_at,
                is_deleted, formula, is_public
            ) VALUES (
                :row_id, NULL, :group_id, 'Invalid group', 's3://invalid',
                NOW(), false, 'H2O', false
            )
            """,
            "fk_structures_group_id",
            {"row_id": uuid.uuid4(), "group_id": uuid.uuid4()},
        )
        _assert_constraint(
            session,
            """
            INSERT INTO public.requests (
                request_id, status, request_type, requested_at, expires_at,
                group_id
            ) VALUES (
                :row_id, 'pending', 'join_request', NOW(),
                NOW() + INTERVAL '7 days', :group_id
            )
            """,
            "requests_group_id_fkey",
            {"row_id": uuid.uuid4(), "group_id": uuid.uuid4()},
        )
        _assert_constraint(
            session,
            """
            INSERT INTO public.tags (tag_id, user_sub, name)
            VALUES (:row_id, 'auth0|681d382c228898b5ba13b7be', 'tag1')
            """,
            "uq_tags_user_sub_name",
            {"row_id": uuid.uuid4()},
        )
    finally:
        session.close()
