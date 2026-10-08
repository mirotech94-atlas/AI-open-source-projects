import datetime as dt

from sqlalchemy import create_engine, event, DateTime
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from sqlalchemy.types import TypeDecorator
from .config import settings

class Base(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator):
    """DateTime(timezone=True) that survives SQLite's lack of tz-aware storage.

    SQLite has no native timezone-aware column type: a value written as
    tz-aware comes back **naive** once read back (including across
    request/session boundaries), which breaks any comparison against
    dt.datetime.now(dt.timezone.utc). This type normalizes on both sides so
    every value that round-trips through it is UTC and aware:
    - process_bind_param: a naive value is assumed to already be UTC; an
      aware value is converted to UTC. Either way, what hits the DB is UTC.
    - process_result_value: a naive value read back is assumed UTC and
      tagged aware; an aware value is normalized to UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

def make_engine(url: str | None = None):
    url = url or settings.db_url()
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    eng = create_engine(url, future=True, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            # WAL lets readers run during a write, but a bulk rewrite still
            # takes the write lock, and anything that needs to write behind it
            # waits. The driver's default patience is 5s, which the hourly demo
            # refresh exceeded: it rewrites thousands of rows in one
            # transaction, and for a few seconds every hour the dashboard
            # answered 500 "database is locked" instead of waiting a moment.
            # Waiting is always the better answer here -- the work is short and
            # the alternative is a visible error.
            dbapi_conn.execute("PRAGMA busy_timeout=30000")
    return eng

engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
