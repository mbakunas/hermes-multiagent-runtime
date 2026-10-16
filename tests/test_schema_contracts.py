from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from shutil import copytree

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, UniqueConstraint, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import aliased, configure_mappers
from sqlalchemy.schema import CreateTable

from hermes_coordination.storage.models import Base
from hermes_coordination.storage.models.enums import ENUM_TYPES


REVISION_PATH = Path(__file__).parents[1] / "src/hermes_coordination/storage/migrations/versions/initial_schema.py"
spec = spec_from_file_location("phase_one_revision", REVISION_PATH)
revision = module_from_spec(spec)
spec.loader.exec_module(revision)


def test_architecture_packages_import():
    packages = (
        "contracts", "storage", "intake", "registry", "control", "node", "messaging",
        "proposals", "approvals", "artifacts", "api", "observability", "config", "cli",
        "adapters", "adapters.hermes_agent",
    )
    for package in packages:
        assert import_module(f"hermes_coordination.{package}") is not None


def test_complete_table_catalog_matches_frozen_migration():
    assert len(Base.metadata.tables) == 33
    assert set(Base.metadata.tables) == set(revision.TABLE_COLUMNS) | {"agent_placements"}
    configure_mappers()


def test_relationships_compile_for_postgresql():
    configure_mappers()
    expected = {
        "agents": {"instances", "placements"},
        "nodes": {"instances", "placements"},
        "agent_instances": {"agent", "node", "capabilities"},
        "capability_definitions": {"inventories"},
        "instance_capabilities": {"instance", "capability"},
        "requests": {"initiator", "concierge", "intent", "workflows"},
        "workflows": {"request", "tasks"},
        "tasks": {"workflow", "attempts", "current_attempt", "capabilities", "prerequisites", "dependents"},
        "task_capabilities": {"task", "capability"},
        "task_dependencies": {"prerequisite", "dependent"},
        "assignments": {"task", "instance", "assigner", "attempt"},
        "task_attempts": {"task", "assignment", "execution_run", "result"},
        "execution_runs": {"attempt", "profile"},
        "task_results": {"attempt", "artifact_references"},
        "work_proposals": {"proposing_agent", "proposing_instance", "originating_task", "originating_attempt"},
        "proposal_tasks": {"proposal", "task"},
        "proposal_decisions": {"proposal", "orchestrator"},
        "artifact_metadata": {"producing_task", "producing_attempt", "uploader", "supersedes"},
        "approval_policies": {"requests"},
        "approval_requests": {"task", "attempt", "policy", "requester", "decisions", "effect"},
        "approval_decisions": {"request", "user"},
        "effect_intents": {"task", "attempt", "approval"},
        "task_events": {"task", "actor_principal", "correlation", "causation"},
        "audit_events": {"entity", "actor_principal", "correlation", "causation"},
        "outbox_events": {"entity"},
    }
    for mapper in Base.registry.mappers:
        if mapper.local_table.name in expected:
            assert expected[mapper.local_table.name] <= set(mapper.relationships.keys())
        for navigation in mapper.relationships:
            target = aliased(navigation.mapper.class_)
            statement = select(mapper.class_).join(getattr(mapper.class_, navigation.key).of_type(target))
            assert str(statement.compile(dialect=postgresql.dialect()))
            assert "delete" not in navigation.cascade
            assert "delete-orphan" not in navigation.cascade


def test_composite_relationships_preserve_binding_columns():
    from hermes_coordination.storage.models import (
        ApprovalRequest, EffectIntent, Task, TaskAttempt, WorkProposal,
    )

    bindings = (
        (Task, "current_attempt", {("current_attempt_id", "id"), ("id", "task_id")}),
        (TaskAttempt, "assignment", {("assignment_id", "id"), ("task_id", "task_id")}),
        (WorkProposal, "proposing_instance", {("proposing_instance_id", "id"), ("proposing_agent_id", "agent_id")}),
        (WorkProposal, "originating_attempt", {("originating_attempt_id", "id"), ("originating_task_id", "task_id")}),
        (ApprovalRequest, "policy", {("policy_id", "id"), ("policy_revision", "revision")}),
        (EffectIntent, "approval", {("approval_request_id", "id"), ("task_id", "task_id"), ("action_digest", "action_digest")}),
    )
    for model, name, expected in bindings:
        navigation = inspect(model).relationships[name]
        assert {(local.name, remote.name) for local, remote in navigation.local_remote_pairs} == expected
        assert navigation.viewonly
        assert navigation.lazy == "raise"


def test_alembic_revision_template_renders(tmp_path):
    scripts = tmp_path / "migrations"
    copytree(REVISION_PATH.parents[1], scripts)
    config = Config(str(scripts / "alembic.ini"))
    config.set_main_option("script_location", str(scripts))
    previous_head = ScriptDirectory.from_config(config).get_current_head()
    generated = command.revision(config, message="Verify revision generation")
    assert generated is not None
    assert generated.down_revision == previous_head
    assert callable(generated.module.upgrade)
    assert callable(generated.module.downgrade)


def test_uuid_server_defaults_and_foreign_keys():
    for table in Base.metadata.tables.values():
        expected_key = [table.c.id, table.c.created_at] if table.name in {
            "audit_events", "task_events", "outbox_events"
        } else [table.c.id]
        assert list(table.primary_key.columns) == expected_key
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
        expected_unique = set(revision.UNIQUE.get(table_name, ()))
        if table_name == "approval_requests":
            expected_unique.add(("id", "task_id", "action_digest"))
        if table_name == "agent_placements":
            expected_unique.add(("agent_id", "node_id"))
        if table_name == "effect_intents":
            expected_unique.add(("approval_request_id",))
        assert actual_unique == expected_unique
        actual_checks = {str(item.sqltext) for item in table.constraints if isinstance(item, CheckConstraint)}
        expected_checks = {expression for _, expression in revision.CHECKS.get(table_name, ())}
        for field in revision.TABLE_COLUMNS.get(table_name, ()):
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
        if table_name == "effect_intents":
            expected.remove((("approval_request_id", "task_id", "attempt_id", "action_digest"),
                             "approval_requests", ("id", "task_id", "attempt_id", "action_digest")))
            expected.add((("approval_request_id", "task_id", "action_digest"),
                          "approval_requests", ("id", "task_id", "action_digest")))
        if table_name == "agent_placements":
            expected.update({(("agent_id",), "agents", ("id",)), (("node_id",), "nodes", ("id",))})
        if table_name not in {"coordination_entities", "audit_events", "task_events", "outbox_events"}:
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
        if table_name == "agent_placements":
            expected.update({("ix_agent_placements_agent_id", ("agent_id",), False, None),
                             ("ix_agent_placements_node_id", ("node_id",), False, None)})
        assert actual == expected, table_name


def test_event_partition_and_placement_contracts():
    for name in ("audit_events", "task_events", "outbox_events"):
        assert Base.metadata.tables[name].dialect_options["postgresql"]["partition_by"] == "RANGE (created_at)"
    placement = Base.metadata.tables["agent_placements"]
    assert set(placement.c.keys()) == {"id", "agent_id", "node_id", "enabled", "created_at", "updated_at"}
    assert str(placement.c.enabled.server_default.arg) == "true"
    assert all(not column.nullable for column in placement.c)


def test_offline_upgrade_still_emits_complete_sql(monkeypatch):
    monkeypatch.setenv("HERMES_DATABASE_URL", "postgresql+psycopg://unused/unused")
    output = StringIO()
    config = Config(str(REVISION_PATH.parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    generated = output.getvalue()
    assert "hermes_archive_events" in generated
    assert "PARTITION BY RANGE (created_at)" in generated
    assert "agent_placements" in generated
    assert "hermes_validate_agent_lifecycle" in generated


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