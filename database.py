import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from dotenv import load_dotenv
load_dotenv()

Base = declarative_base()

_engine = None
_SessionLocal = None


def get_database_url() -> str:
    database_user = os.getenv('DATABASE_USER')
    database_password = os.getenv('DATABASE_PASSWORD')
    database_host = os.getenv('DATABASE_HOST')
    database_port = os.getenv('DATABASE_PORT')
    database_name = os.getenv('DATABASE_NAME')

    if not all([database_user, database_password, database_host, database_port, database_name]):
        raise EnvironmentError("One or more database environment variables are not set.")

    return (
        f'postgresql://{database_user}:{database_password}'
        f'@{database_host}:{database_port}/{database_name}'
    )


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
    Base.metadata.create_all(bind=get_engine(), checkfirst=True)
