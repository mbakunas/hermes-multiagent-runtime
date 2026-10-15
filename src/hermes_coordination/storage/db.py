from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from os import environ

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class Database:
    engine: Engine
    sessions: sessionmaker[Session]

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions.begin() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()


def create_database(url: str | None = None, *, pool_size: int = 5, max_overflow: int = 2) -> Database:
    configured_url = url or environ.get("HERMES_DATABASE_URL")
    if not configured_url:
        raise ValueError("HERMES_DATABASE_URL is required")
    parsed_url = make_url(configured_url)
    if parsed_url.drivername != "postgresql+psycopg":
        raise ValueError("Only postgresql+psycopg is supported")
    engine = create_engine(
        parsed_url, pool_size=pool_size, max_overflow=max_overflow,
        pool_pre_ping=True, pool_timeout=10, hide_parameters=True,
        connect_args={
            "connect_timeout": 10,
            "options": "-c timezone=UTC -c search_path=public -c lock_timeout=5s "
                       "-c statement_timeout=30s -c idle_in_transaction_session_timeout=30s "
                       "-c transaction_timeout=60s",
        },
    )

    @event.listens_for(engine, "connect")
    def verify_version(connection, connection_record) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::integer")
            if cursor.fetchone()[0] < 170000:
                raise RuntimeError("PostgreSQL 17 or newer is required")
        connection.rollback()

    return Database(engine, sessionmaker(engine, expire_on_commit=False))