from sqlalchemy.orm import Session
from database import SessionLocal

def get_db() -> Session:
    """
    Provides a SQLAlchemy DB session to FastAPI routes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
