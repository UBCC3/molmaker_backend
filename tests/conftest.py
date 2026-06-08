import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import uuid
from datetime import datetime, timezone

from auth import verify_token
from database import Base
from dependencies import get_db
from main import create_app
from models import Group, Job, Request, Structure, Tags, User

# --- In-memory SQLite test database ---

SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
    Create a clean in-memory database session for each test, tears it down after.
    """
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


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
            "member_since": datetime.now(timezone.utc),
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
            "location": "s3://test-bucket/structures/test.xyz",
            "notes": None,
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
    def create_job(structures=None, tags=None, **overrides):
        values = {
            "job_id": uuid.uuid4(),
            "job_name": f"Job {uuid.uuid4().hex[:8]}",
            "filename": "input.xyz",
            "status": "pending",
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
        }
        values.update(overrides)
        job = Job(**values)
        if structures:
            job.structures.extend(structures)
        if tags:
            job.tags.extend(tags)
        return _save(db, job)

    return create_job


@pytest.fixture
def request_factory(db):
    """
    Factory for persisted Request rows between two users and a group.
    """
    def create_request(sender, receiver, group, **overrides):
        values = {
            "request_id": uuid.uuid4(),
            "status": "pending",
            "requested_at": datetime.now(timezone.utc),
            "sender_sub": sender.user_sub if hasattr(sender, "user_sub") else sender,
            "receiver_sub": receiver.user_sub if hasattr(receiver, "user_sub") else receiver,
            "group_id": group.group_id if hasattr(group, "group_id") else group,
        }
        values.update(overrides)
        return _save(db, Request(**values))

    return create_request
