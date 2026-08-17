from sqlalchemy import inspect

import database
from conftest import engine


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
