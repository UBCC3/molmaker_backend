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
LEGACY_SCHEMA_PATH = PROJECT_ROOT / "tests" / "fixtures" / "pre_pr14_schema.sql"
DUMP_PATH = PROJECT_ROOT / "molmaker.sql"

GROUP_ID = "00000000-0000-0000-0000-000000000001"
OLD_JOB_ID = "10000000-0000-0000-0000-000000000001"
NEW_JOB_ID = "10000000-0000-0000-0000-000000000002"
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
        tables = (
            "groups",
            "users",
            "jobs",
            "structures",
            "requests",
            "tags",
            "jobs_structures",
            "jobs_tags",
            "structures_tags",
        )
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
            "structures": 9,
            "requests": 3,
            "tags": 11,
        }

        assert "group_id" in _column_names(session, "jobs")
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
        assert {
            "uq_requests_pending_invite",
            "uq_requests_pending_join",
            "uq_requests_pending_demember",
        } <= _index_names(session)
    finally:
        session.close()


def test_migration_is_safe_after_restoring_molmaker_dump(db):
    _restore_dump(db)
    state_before_migration = _database_state()

    _run_sql_file(MIGRATION_PATH)

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
