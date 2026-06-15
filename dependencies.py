from sqlalchemy.orm import Session
from database import get_session_local

def get_db() -> Session:
    """
    Provides a SQLAlchemy DB session to FastAPI routes.
    """
    db = get_session_local()()
    try:
        yield db
    finally:
        db.close()
