from logging.config import fileConfig
from os import environ

from alembic import context
from sqlalchemy import create_engine, pool, text

from hermes_coordination.storage.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    url = environ.get("HERMES_DATABASE_URL")
    if not url:
        raise RuntimeError("Set HERMES_DATABASE_URL to a PostgreSQL psycopg URL")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("HERMES_DATABASE_URL must use postgresql+psycopg")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        transactional_ddl=True, compare_type=True, compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        configure_connection(supplied_connection)
        return
    engine = create_engine(
        database_url(), poolclass=pool.NullPool,
        connect_args={"options": "-c timezone=UTC -c lock_timeout=5s"},
        hide_parameters=True,
    )
    try:
        with engine.connect() as connection:
            configure_connection(connection)
    finally:
        engine.dispose()


def configure_connection(connection) -> None:
    if connection.dialect.server_version_info < (17,):
        raise RuntimeError("PostgreSQL 17 or newer is required")
    partitions: set[str] = set()

    def include_name(name, type_, parent_names) -> bool:
        return type_ != "table" or name not in partitions

    context.configure(
        connection=connection, target_metadata=target_metadata,
        transactional_ddl=True, compare_type=True, compare_server_default=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        connection.execute(text("SELECT pg_advisory_xact_lock(734821, 1)"))
        partitions.update(connection.scalars(text("""
            SELECT child.relname
            FROM pg_catalog.pg_inherits inheritance
            JOIN pg_catalog.pg_class parent ON parent.oid = inheritance.inhparent
            JOIN pg_catalog.pg_namespace namespace ON namespace.oid = parent.relnamespace
            JOIN pg_catalog.pg_class child ON child.oid = inheritance.inhrelid
            WHERE namespace.nspname = 'public' AND child.relispartition
                AND parent.relname IN ('audit_events', 'task_events', 'outbox_events')
        """)))
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()