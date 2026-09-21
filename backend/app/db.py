from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .config import database_url

engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
Session = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with Session.begin() as db:
        yield db


def write_lock(db):
    # Short domain writes share one transaction lock. This deliberately serializes
    # changes across user/account limits, ACLs and batch deletion for a small pool.
    # No lock, queue or business state lives in an API process.
    db.execute(text("SELECT pg_advisory_xact_lock(71820419)"))
