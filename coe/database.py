import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://user:pass@localhost:5433/coe",
)

engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True) #turn on echo for debugging
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def get_session() -> Session:
    """
    Get a new databse session. Caller is responsible for closing the session when done.
    """
    return SessionLocal()