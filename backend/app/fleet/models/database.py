"""
Database engine and session management.
Uses SQLAlchemy with async-compatible session factory.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.fleet.config.settings import settings

engine = create_engine(
    settings.db.DB_URL,
    echo=settings.db.ECHO_SQL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.db.DB_URL else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables in the database."""
    import os
    if "sqlite:///" in settings.db.DB_URL:
        db_path = settings.db.DB_URL.replace("sqlite:///", "")
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    Base.metadata.create_all(bind=engine)
