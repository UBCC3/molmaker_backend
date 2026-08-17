import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from auth import verify_token
from database import Base
from dependencies import get_db
from enum_types import JobStatus
from main import create_app
from models import Group, Job, JobInput, JobResult, Request, Structure, Tags, User
from settings import get_settings


@pytest.fixture(autouse=True)
def clear_backend_settings_cache():
    """Keep environment overrides isolated between tests."""

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

# --- Test database ---

SQLALCHEMY_TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
if not SQLALCHEMY_TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL must identify a dedicated PostgreSQL test database"
    )

test_database_url = make_url(SQLALCHEMY_TEST_DATABASE_URL)
if test_database_url.get_backend_name() != "postgresql":
    raise RuntimeError("TEST_DATABASE_URL must use PostgreSQL")

TEST_SCHEMA = f"molmaker_test_{uuid.uuid4().hex}"
schema_engine = create_engine(test_database_url)
engine = create_engine(
    test_database_url,
    connect_args={"options": f"-csearch_path={TEST_SCHEMA}"},
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def isolated_postgresql_schema():
    """Create and remove one isolated PostgreSQL schema per test run."""

    with schema_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{TEST_SCHEMA}"')
    try:
        yield
    finally:
        engine.dispose()
        with schema_engine.begin() as connection:
            connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'
            )
        schema_engine.dispose()


def _save(db, instance):
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


def make_auth0_payload(
    user_sub: str,
    email: str = None,
    role: str = "member",
    group_id=None,
):
    """
    Build the fake Auth0 payload returned by the mocked auth dependency.
    """
    payload = {
        "sub": user_sub,
        "email": email or f"{user_sub}@test.com",
        "aud": "test-audience",
        "iss": "https://test.auth0.com/",
    }
    if role is not None:
        payload["role"] = role
    if group_id is not None:
        payload["group_id"] = group_id
    return payload


@pytest.fixture
def app():
    """
    Create a fresh FastAPI app whose dependencies can be overridden per test.
    """
    app = create_app()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    """
    Create a clean database session for each test, then remove its tables.
    """
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sql_statements():
    """Collect SQL statements so list tests can catch per-row queries."""
    statements = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)


@pytest.fixture
def auth_user():
    """
    Default authenticated user payload for API tests.
    """
    return make_auth0_payload("auth0|testuser")


@pytest.fixture
def set_auth_user(app):
    """
    Replace the authenticated user payload inside a test.
    """
    def _set_auth_user(payload):
        app.dependency_overrides[verify_token] = lambda: payload
        return payload

    return _set_auth_user


@pytest.fixture
def client(app, db, auth_user):
    """
    Test client with DB and auth dependencies overridden.
    """
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_token] = lambda: auth_user
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def group_factory(db):
    """
    Factory for persisted Group rows with overridable fields.
    """
    def create_group(**overrides):
        values = {
            "group_id": uuid.uuid4(),
            "name": f"Test Group {uuid.uuid4().hex[:8]}",
        }
        values.update(overrides)
        return _save(db, Group(**values))

    return create_group


@pytest.fixture
def user_factory(db):
    """
    Factory for persisted User rows, optionally attached to a Group.
    """
    def create_user(group=None, **overrides):
        user_sub = overrides.pop("user_sub", f"auth0|{uuid.uuid4().hex}")
        values = {
            "user_sub": user_sub,
            "email": f"{user_sub.replace('|', '_')}@test.com",
            "role": "member",
            "group_id": group.group_id if group is not None else None,
            "role_or_group_updated_at": datetime.now(timezone.utc),
        }
        values.update(overrides)
        return _save(db, User(**values))

    return create_user


@pytest.fixture
def tag_factory(db):
    """
    Factory for persisted Tags rows.
    """
    def create_tag(**overrides):
        values = {
            "tag_id": uuid.uuid4(),
            "user_sub": "auth0|testuser",
            "name": f"tag-{uuid.uuid4().hex[:8]}",
        }
        values.update(overrides)
        return _save(db, Tags(**values))

    return create_tag


@pytest.fixture
def structure_factory(db):
    """
    Factory for persisted Structure rows, with optional tag relationships.
    """
    def create_structure(tags=None, **overrides):
        values = {
            "structure_id": uuid.uuid4(),
            "user_sub": "auth0|testuser",
            "name": f"Structure {uuid.uuid4().hex[:8]}",
            "formula": "H2O",
            "notes": None,
            "content": "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n",
            "thumbnail": b"thumbnail-bytes",
            "thumbnail_media_type": "image/png",
            "uploaded_at": datetime.now(timezone.utc),
            "is_deleted": False,
        }
        values.update(overrides)
        structure = Structure(**values)
        if tags:
            structure.tags.extend(tags)
        return _save(db, structure)

    return create_structure


@pytest.fixture
def job_factory(db):
    """
    Factory for persisted Job rows, with optional structure and tag relationships.
    """
    def create_job(
        structures=None,
        tags=None,
        *,
        input_xyz="1\n\nH 0 0 0\n",
        keywords=None,
        with_input=True,
        **overrides,
    ):
        values = {
            "job_id": uuid.uuid4(),
            "job_name": f"Job {uuid.uuid4().hex[:8]}",
            "filename": "input.xyz",
            "status": JobStatus.submitting.value,
            "calculation_type": "energy",
            "method": "hf",
            "basis_set": "sto-3g",
            "charge": 0,
            "multiplicity": 1,
            "submitted_at": datetime.now(timezone.utc),
            "user_sub": "auth0|testuser",
            "is_deleted": False,
            "is_public": False,
            "is_uploaded": False,
            "attempt_count": 0,
            "cancel_requested": False,
        }
        values.update(overrides)
        job = Job(**values)
        if with_input:
            job.job_input = JobInput(
                job_id=job.job_id,
                input_xyz=input_xyz,
                keywords=keywords,
            )
        if structures:
            job.structures.extend(structures)
        if tags:
            job.tags.extend(tags)
        return _save(db, job)

    return create_job


@pytest.fixture
def job_result_factory(db, job_factory):
    """Factory for the one-to-one result row of a persisted job."""

    def create_job_result(job=None, **overrides):
        job = job or job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
        )
        values = {
            "job_id": job.job_id,
            "result": {"success": True},
            "error": None,
            "artifacts": {},
        }
        values.update(overrides)
        return _save(db, JobResult(**values))

    return create_job_result


@pytest.fixture
def request_factory(db):
    """
    Factory for persisted Request rows between two users and a group.
    """
    def create_request(sender, receiver, group, **overrides):
        sender_sub = sender.user_sub if hasattr(sender, "user_sub") else sender
        receiver_sub = receiver.user_sub if hasattr(receiver, "user_sub") else receiver
        values = {
            "request_id": uuid.uuid4(),
            "status": "pending",
            "request_type": "invite",
            "requested_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
            "resolved_at": None,
            "sender_sub": sender_sub,
            "receiver_sub": receiver_sub,
            "created_by_sub": sender_sub or receiver_sub,
            "resolved_by_sub": None,
            "group_id": group.group_id if hasattr(group, "group_id") else group,
            "sender_email_snapshot": None,
            "receiver_email_snapshot": None,
            "created_by_email_snapshot": None,
            "resolved_by_email_snapshot": None,
            "group_name_snapshot": None,
        }
        values.update(overrides)
        return _save(db, Request(**values))

    return create_request
