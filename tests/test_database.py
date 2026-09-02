import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TEST_SCHEMA, engine, test_database_url
from sqlalchemy import inspect

import database
from database import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
