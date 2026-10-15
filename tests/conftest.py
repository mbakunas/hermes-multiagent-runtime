from os import environ
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

from alembic import command
from alembic.config import Config
from psycopg import sql
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


@pytest.fixture(scope="session")
def engine():
    url = environ.get("HERMES_TEST_DATABASE_URL")
    if not url:
        if environ.get("CI"):
            pytest.fail("CI requires HERMES_TEST_DATABASE_URL; integration tests must not be skipped")
        pytest.skip("Set HERMES_TEST_DATABASE_URL to an empty disposable PostgreSQL 17+ database")
    database = create_engine(url, poolclass=NullPool, hide_parameters=True,
                             connect_args={"options": "-c timezone=UTC -c lock_timeout=10s -c statement_timeout=30s"})
    with database.begin() as connection:
        if connection.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")):
            raise RuntimeError("Database integration tests require an empty disposable database")
        config = Config(str(Path(__file__).parents[1] / "src/hermes_coordination/storage/migrations/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_phase1")
        connection.exec_driver_sql("""
            UPDATE outbox_events SET publication_status = 'published'
            WHERE id = (SELECT id FROM outbox_events ORDER BY created_at, id LIMIT 1)
        """)
        legacy_events = {table: connection.execute(text(
            f"SELECT id,created_at FROM {table} ORDER BY id")).all()
            for table in ("audit_events", "task_events", "outbox_events")}
        command.upgrade(config, "head")
        for table, identities in legacy_events.items():
            assert connection.execute(text(f"SELECT id,created_at FROM {table} ORDER BY id")).all() == identities
        connection.exec_driver_sql("GRANT hermes_orchestrator TO CURRENT_USER")
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture(scope="session")
def services(engine):
    connections = {"actors": {}}
    logins = []
    with engine.begin() as connection:
        connections["node_id"] = connection.scalar(text("""
            INSERT INTO nodes(name, enrollment_identity, desired_state)
            VALUES (gen_random_uuid()::text, gen_random_uuid()::text, 'enabled') RETURNING id
        """))
        for service in ("node", "intake", "approval", "outbox", "orchestrator", "admin"):
            login = f"hermes_test_{service}_{uuid4().hex[:8]}"
            password = token_urlsafe(32)
            connection.exec_driver_sql(sql.SQL(
                "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
            ).format(sql.Identifier(login), sql.Literal(password)).as_string())
            connection.exec_driver_sql(sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(f"hermes_{service}"), sql.Identifier(login)).as_string())
            connections["actors"][service] = connection.scalar(text("""
                INSERT INTO principals(subject, kind, database_role, node_id)
                VALUES (:login, CAST(:kind AS public."PrincipalKind"), :login, :node) RETURNING id
            """), {"login": login, "kind": "node" if service == "node" else "service",
                    "node": connections["node_id"] if service == "node" else None})
            connections[service] = create_engine(
                engine.url.set(username=login, password=password), poolclass=NullPool, hide_parameters=True,
                connect_args={"application_name": login,
                              "options": "-c timezone=UTC -c lock_timeout=10s -c statement_timeout=30s"},
            )
            logins.append(login)
    try:
        yield connections
    finally:
        for service in ("node", "intake", "approval", "outbox", "orchestrator", "admin"):
            connections[service].dispose()
        with engine.begin() as connection:
            for login in logins:
                connection.exec_driver_sql(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)).as_string())