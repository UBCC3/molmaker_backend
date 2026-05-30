import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch
import uuid
from datetime import datetime, timezone

from database import Base
from dependencies import get_db
from models import User, Group, Job
from main import app

# --- In-memory SQLite test database ---

SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db():
    """
    Creates a fresh DB for each test, tears it down after.
    """
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db):
    """
    Test client with DB dependency overridden to use test DB.
    """
    def override_get_db():
        try:
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

# --- Reusable mock payloads (what verify_token would return) ---

def make_auth0_payload(user_sub: str, email: str = None):
    return{
        "sub": user_sub,
        "email": email or f"{user_sub}@test.com",
        "aud": "test-audience",
        "iss": "https://test.auth0.com/",
    }

# --- Reusable user/group factory fixtures

@pytest.fixture
def test_group(db):
    group = Group(
        group_id=uuid.uuid4(),
        name="Test Group"
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group

@pytest.fixture
def test_user(db):
    user = User(
        user_sub="auth0|testuser",
        email="testuser@test.com",
        role="member",
        group_id="test_group.group_id",
        member_since=datetime.now(timezone.utc)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def test_admin(db, test_group):
    admin = User(
        user_sub="auth0|adminuser",
        email="admin@test.com",
        role="group_admin",
        group_id=test_group.group_id,
        member_since=datetime.now(timezone.utc)
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin

@pytest.fixture
def test_user_no_group(db):
    user = User(
        user_sub="auth0|nogroupuser",
        email="nogroup@test.com",
        role="member",
        group_id=None,
        member_since=datetime.now(timezone.utc)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user