import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TEST_SCHEMA, engine, test_database_url
from sqlalchemy import inspect, text

import database
from database import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_STORAGE_MIGRATION = (
    PROJECT_ROOT / "migrations" / "20260902_add_archive_storage_service.sql"
)


def test_init_db_creates_the_current_schema(db, monkeypatch):
    monkeypatch.setattr(database, "get_engine", lambda: engine)

    database.init_db()

    inspector = inspect(engine)
    assert {"jobs", "job_inputs", "job_results", "structures"} <= set(
        inspector.get_table_names()
    )
    structure_columns = {
        column["name"]: column for column in inspector.get_columns("structures")
    }
    assert "location" not in structure_columns
    assert structure_columns["content"]["nullable"] is False
    assert structure_columns["thumbnail"]["nullable"] is False
    assert structure_columns["thumbnail_media_type"]["nullable"] is False
    job_columns = {column["name"]: column for column in inspector.get_columns("jobs")}
    assert job_columns["archive_upload_requested"]["nullable"] is False
    assert job_columns["archive_uploaded"]["nullable"] is False
    assert job_columns["archive_upload_status"]["nullable"] is False
    assert job_columns["archive_storage_service"]["nullable"] is False


def test_database_module_entrypoint_creates_the_current_schema():
    connection_values = {
        "DATABASE_USER": test_database_url.username,
        "DATABASE_PASSWORD": test_database_url.password,
        "DATABASE_HOST": test_database_url.host,
        "DATABASE_PORT": str(test_database_url.port or 5432),
        "DATABASE_NAME": test_database_url.database,
    }
    if not all(connection_values.values()):
        pytest.skip("Database entry-point test requires a complete connection URL")

    environment = os.environ.copy()
    environment.update(connection_values)
    environment["PGOPTIONS"] = f"-csearch_path={TEST_SCHEMA}"

    Base.metadata.drop_all(bind=engine)
    try:
        subprocess.run(
            [sys.executable, "-m", "database"],
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
        )

        assert {"jobs", "job_inputs", "job_results", "structures"} <= set(
            inspect(engine).get_table_names()
        )
    finally:
        Base.metadata.drop_all(bind=engine)


def test_archive_storage_migration_preserves_and_backfills_existing_jobs(
    db,
    user_factory,
    job_factory,
):
    user_factory(user_sub="auth0|testuser")
    existing_job = job_factory()
    job_id = existing_job.job_id

    db.execute(text("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_archive_storage_service"))
    db.execute(text("ALTER TABLE jobs DROP COLUMN archive_storage_service"))
    db.commit()

    migration_sql = ARCHIVE_STORAGE_MIGRATION.read_text(encoding="utf-8")
    db.connection().exec_driver_sql(migration_sql)
    db.commit()

    storage_service = db.execute(
        text("SELECT archive_storage_service FROM jobs WHERE job_id = :job_id"),
        {"job_id": job_id},
    ).scalar_one()
    assert storage_service == "garage"

    job_columns = {
        column["name"]: column for column in inspect(engine).get_columns("jobs")
    }
    assert job_columns["archive_storage_service"]["nullable"] is False
    assert "s3" in str(job_columns["archive_storage_service"]["default"])
    assert "ck_jobs_archive_storage_service" in {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints("jobs")
    }

    # The migration can be rerun safely during a deployment retry.
    db.connection().exec_driver_sql(migration_sql)
    db.commit()
