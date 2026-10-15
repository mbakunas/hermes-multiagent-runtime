from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

from hermes_coordination.storage.models import Base
from hermes_coordination.storage.models.enums import ENUM_TYPES


REVISION_PATH = Path(__file__).parents[1] / "src/hermes_coordination/storage/migrations/versions/initial_schema.py"
spec = spec_from_file_location("phase_one_revision", REVISION_PATH)
revision = module_from_spec(spec)
spec.loader.exec_module(revision)


def test_complete_table_catalog_matches_frozen_migration():
    assert len(Base.metadata.tables) == 32
    assert set(Base.metadata.tables) == set(revision.TABLE_COLUMNS)
    configure_mappers()


def test_uuid_server_defaults_and_foreign_keys():
    for table in Base.metadata.tables.values():
        assert list(table.primary_key.columns) == [table.c.id]
        assert isinstance(table.c.id.type, postgresql.UUID)
        assert table.c.id.type.as_uuid
        assert str(table.c.id.server_default.arg) == "gen_random_uuid()"
        for constraint in table.foreign_key_constraints:
            assert constraint.deferrable
            assert constraint.initially == "DEFERRED"
            assert constraint.ondelete == "RESTRICT"
            assert all(element.column.table.name in Base.metadata.tables for element in constraint.elements)


def test_model_columns_match_frozen_revision():
    for table_name, frozen_columns in revision.TABLE_COLUMNS.items():
        model_columns = Base.metadata.tables[table_name].columns
        assert set(model_columns.keys()) == {"id", *(field.name for field in frozen_columns)}
        for field in frozen_columns:
            model = model_columns[field.name]
            assert model.nullable == field.nullable, (table_name, field.name)
            assert str(model.type.compile(dialect=postgresql.dialect())) == str(field.type.compile(dialect=postgresql.dialect()))
            model_default = None if model.server_default is None else str(model.server_default.arg)
            frozen_default = None if field.server_default is None else str(field.server_default.arg)
            assert model_default == frozen_default, (table_name, field.name)


def test_enums_match_frozen_revision():
    assert {enum.__name__: tuple(member.value for member in enum) for enum in ENUM_TYPES} == revision.ENUMS


def test_unique_and_check_constraints_match_revision():
    for table_name, table in Base.metadata.tables.items():
        actual_unique = {tuple(column.name for column in item.columns) for item in table.constraints
                         if isinstance(item, UniqueConstraint)}
        assert actual_unique == set(revision.UNIQUE.get(table_name, ()))
        actual_checks = {str(item.sqltext) for item in table.constraints if isinstance(item, CheckConstraint)}
        expected_checks = {expression for _, expression in revision.CHECKS.get(table_name, ())}
        for field in revision.TABLE_COLUMNS[table_name]:
            if isinstance(field.type, postgresql.JSONB):
                shape = "array" if field.name == "capability_snapshot" else "object"
                limit = 8192 if field.name == "envelope" else 65536
                expected_checks.add(f"jsonb_typeof({field.name}) = '{shape}' AND octet_length({field.name}::text) <= {limit}")
        assert actual_checks == expected_checks, table_name


def test_foreign_key_edges_match_revision():
    for table_name, table in Base.metadata.tables.items():
        actual = {(tuple(element.parent.name for element in constraint.elements),
                   constraint.elements[0].column.table.name,
                   tuple(element.column.name for element in constraint.elements))
                  for constraint in table.constraints if isinstance(constraint, ForeignKeyConstraint)}
        expected = set()
        for local, remote_table, remote in revision.FOREIGN_KEYS.get(table_name, ()):
            expected.add(((local,) if isinstance(local, str) else local,
                          remote_table, (remote,) if isinstance(remote, str) else remote))
        if table_name != "coordination_entities":
            expected.add((("id",), "coordination_entities", ("id",)))
        assert actual == expected, table_name


def test_indexes_match_revision():
    for table_name, table in Base.metadata.tables.items():
        actual = {(index.name, tuple(column.name for column in index.columns), bool(index.unique),
                   None if index.dialect_options["postgresql"]["where"] is None
                   else str(index.dialect_options["postgresql"]["where"])) for index in table.indexes}
        expected = {(f"ix_{table_name}_{field}", (field,), False, None)
                    for field in revision.SIMPLE_INDEXES.get(table_name, ())}
        expected.update(revision.INDEXES.get(table_name, ()))
        assert actual == expected, table_name


def test_tables_compile_for_postgresql_and_no_blob_columns():
    for table in Base.metadata.tables.values():
        assert str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for column in table.columns:
            assert not isinstance(column.type, postgresql.BYTEA)


def test_timestamps_are_timezone_aware_and_server_generated():
    lifecycle = {"created_at", "updated_at", "accepted_at", "verified_at", "started_at"}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone
            if column.name in lifecycle and not column.nullable:
                assert str(column.server_default.arg) == "CURRENT_TIMESTAMP"