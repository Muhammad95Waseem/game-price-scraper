import os
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("CRITICAL error: DATABASE_URL environment variable is missing!")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Session = scoped_session(session_factory)
Base = declarative_base()

class DBCompatibilityWrapper:
    @property
    def session(self):
        return Session()

    @staticmethod
    def select(*args, **kwargs):
        return sqlalchemy.select(*args, **kwargs)

db = DBCompatibilityWrapper()