from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from settings import get_settings

Base = declarative_base()

_engine = None
_SessionLocal = None


def get_database_url() -> str:
    return get_settings().database_url()


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url())
    return _engine


def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal


def init_db():
    """Create the current schema in an empty database."""

    import models  # noqa: F401 - registers every table with Base.metadata

    Base.metadata.create_all(bind=get_engine(), checkfirst=True)


if __name__ == "__main__":
    init_db()
