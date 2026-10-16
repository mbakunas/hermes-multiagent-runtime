from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_populated_upgrade_preserves_events_and_publication_provenance(disposable_database):
    with disposable_database.begin() as connection:
        config = Config(str(Path(__file__).parents[1] / "src/hermes_coordination/storage/migrations/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_phase1")
        connection.exec_driver_sql("""
            UPDATE outbox_events SET publication_status='published'
            WHERE id=(SELECT id FROM outbox_events ORDER BY created_at,id LIMIT 1)
        """)
        identities = {table: connection.execute(text(f"SELECT id,created_at FROM {table} ORDER BY id")).all()
                      for table in ("audit_events", "task_events", "outbox_events")}
        command.upgrade(config, "head")
        for table, expected in identities.items():
            assert connection.execute(text(f"SELECT id,created_at FROM {table} ORDER BY id")).all() == expected
        assert connection.scalar(text("""
            SELECT count(*) FROM audit_events WHERE action='publication.changed'
                AND entity_type <> 'outbox_events' AND payload ? 'event_id'
        """)) == 1


@pytest.fixture
def connection(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql("GRANT hermes_orchestrator TO CURRENT_USER")
        try:
            yield connection
        finally:
            transaction.rollback()


def seed_task(connection, *, node=None, create_instance=True):
    actor = connection.scalar(text("SELECT public.hermes_actor()"))
    artifact = connection.scalar(text("""
        INSERT INTO artifact_metadata(uploader_identity, artifact_kind, media_type, byte_size, checksum_digest,
            export_identifier, environment, storage_key, original_filename, provenance, retention_policy,
            state, verified_at, accepted_at)
        VALUES (:actor, 'markdown', 'text/markdown', 0, repeat('a',64), 'test-export', 'test',
            gen_random_uuid()::text, 'intent.md', '{"origin":"external_intake"}', '{}', 'accepted',
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id
    """), {"actor": actor})
    request = connection.scalar(text("""
        INSERT INTO requests(authenticated_initiator, intent_reference, idempotency_key)
        VALUES (:actor, :artifact, gen_random_uuid()::text) RETURNING id
    """), {"actor": actor, "artifact": artifact})
    workflow = connection.scalar(text("INSERT INTO workflows(request_id, policy_revision) VALUES (:request, 'v1') RETURNING id"),
                                 {"request": request})
    task = connection.scalar(text("""
        INSERT INTO tasks(workflow_id, specification_revision, output_contract, state)
        VALUES (:workflow, 'v1', '{}', 'ready') RETURNING id
    """), {"workflow": workflow})
    if node is None:
        node = connection.scalar(text("""
            INSERT INTO nodes(name, enrollment_identity, desired_state)
            VALUES (gen_random_uuid()::text, gen_random_uuid()::text, 'enabled') RETURNING id
        """))
    agent = connection.scalar(text("""
        INSERT INTO agents(name, role, desired_state, approved_profile_revision)
        VALUES (gen_random_uuid()::text, 'researcher', 'enabled', 'verified') RETURNING id
    """))
    instance = None
    if create_instance:
        connection.execute(text("INSERT INTO agent_placements(agent_id,node_id) VALUES (:agent,:node)"),
                           {"agent": agent, "node": node})
        instance = connection.scalar(text("""
            INSERT INTO agent_instances(agent_id, node_id, hermes_version, verified_profile_digest, state, readiness)
            VALUES (:agent, :node, 'pinned', 'verified', 'ready', 'ready') RETURNING id
        """), {"agent": agent, "node": node})
    return {"actor": actor, "artifact": artifact, "request": request, "workflow": workflow,
            "task": task, "node": node, "agent": agent, "instance": instance}


def seed_assignment(connection, records, *, attempt_number=1):
    assignment = connection.scalar(text("""
        INSERT INTO assignments(task_id, instance_id, authoritative_assigner, selection_rationale, capability_snapshot)
        VALUES (:task, :instance, :actor, 'test', '[]') RETURNING id
    """), records)
    attempt = connection.execute(text("""
        INSERT INTO task_attempts(task_id, assignment_id, attempt_number, lease_expires_at)
        VALUES (:task, :assignment, :attempt_number, CURRENT_TIMESTAMP + interval '60 seconds')
        RETURNING id, ownership_token
    """), {**records, "assignment": assignment, "attempt_number": attempt_number}).one()
    connection.execute(text("UPDATE tasks SET state = 'assigned', current_attempt_id = :attempt WHERE id = :task"),
                       {"attempt": attempt.id, "task": records["task"]})
    connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    return assignment, attempt


def assert_rejected(connection, statement, parameters=None, *, sqlstate="P0001"):
    if statement.lstrip().upper().startswith("TRUNCATE"):
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
    with pytest.raises(DBAPIError) as error:
        with connection.begin_nested():
            connection.execute(text(statement), parameters or {})
            connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    assert error.value.orig.sqlstate == sqlstate


def test_all_tables_enums_and_capabilities_created(connection):
    assert connection.scalar(text("SELECT count(*) FROM capability_definitions")) == 19
    assert connection.scalar(text("""
        SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace
            AND relkind IN ('r','p') AND NOT relispartition
    """)) == 34
    assert connection.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'TaskState'")) == 1
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003_review_followup", \
        "Fresh database migration did not reach the Alembic head"


def test_authority_privileges(connection):
    assert not connection.scalar(text("SELECT has_table_privilege('hermes_node','assignments','INSERT')"))
    assert not connection.scalar(text("SELECT has_table_privilege('hermes_intake','assignments','INSERT')"))
    assert not connection.scalar(text("SELECT has_table_privilege('hermes_approval','assignments','INSERT')"))
    assert connection.scalar(text("SELECT has_table_privilege('hermes_orchestrator','assignments','INSERT')"))
    assert not connection.scalar(text("SELECT has_table_privilege('hermes_node','task_attempts','UPDATE')"))
    assert not connection.scalar(text("SELECT has_function_privilege('hermes_reader','public.hermes_claim_attempt(uuid,uuid,integer)','EXECUTE')"))


def test_primary_keys_and_history_cannot_change(connection):
    records = seed_task(connection)
    assert_rejected(connection, "UPDATE nodes SET id = gen_random_uuid() WHERE id = :node", records)
    assert_rejected(connection, "DELETE FROM tasks WHERE id = :task", records, sqlstate="42501")
    assert_rejected(connection, "TRUNCATE audit_events", sqlstate="42501")
    assert_rejected(connection, "UPDATE audit_events SET action = 'rewritten'")


def test_bounded_json_and_foreign_keys(connection):
    records = seed_task(connection)
    assert_rejected(connection, "UPDATE nodes SET metadata_json = jsonb_build_object('oversized',repeat('x',65537)) WHERE id = :node", records, sqlstate="23514")
    assert_rejected(connection, "INSERT INTO workflows(request_id, policy_revision) VALUES (gen_random_uuid(),'v1')", sqlstate="23503")


def test_single_active_incarnation(connection):
    records = seed_task(connection)
    assert_rejected(connection, """
        INSERT INTO agent_instances(agent_id, node_id, hermes_version, verified_profile_digest, state, readiness)
        VALUES (:agent, :node, 'pinned', 'verified', 'ready', 'ready')
    """, records, sqlstate="23505")


def test_single_active_assignment_and_attempt_binding(connection):
    records = seed_task(connection)
    assignment, attempt = seed_assignment(connection, records)
    assert_rejected(connection, """
        INSERT INTO assignments(task_id, instance_id, authoritative_assigner, selection_rationale, capability_snapshot)
        VALUES (:task, :instance, :actor, 'duplicate', '[]')
    """, records)
    assert_rejected(connection, "UPDATE tasks SET current_attempt_id = gen_random_uuid() WHERE id = :task", records, sqlstate="23503")
    assert_rejected(connection, """
        INSERT INTO task_attempts(task_id, assignment_id, attempt_number, lease_expires_at)
        VALUES (:task, :assignment, 1, CURRENT_TIMESTAMP + interval '60 seconds')
    """, {**records, "assignment": assignment}, sqlstate="23505")
    assert_rejected(connection, "SELECT public.hermes_claim_attempt(:attempt, NULL, 60)", {"attempt": attempt.id}, sqlstate="42501")


def test_dependencies_are_acyclic_and_same_workflow(connection):
    records = seed_task(connection)
    other = connection.scalar(text("""
        INSERT INTO tasks(workflow_id, specification_revision, output_contract) VALUES (:workflow,'v1','{}') RETURNING id
    """), records)
    connection.execute(text("INSERT INTO task_dependencies(prerequisite_task_id,dependent_task_id) VALUES (:task,:other)"),
                       {**records, "other": other})
    assert_rejected(connection, "INSERT INTO task_dependencies(prerequisite_task_id,dependent_task_id) VALUES (:other,:task)",
                    {**records, "other": other})


def test_artifact_content_and_approval_binding_are_immutable(connection):
    records = seed_task(connection)
    assert_rejected(connection, "UPDATE artifact_metadata SET checksum_digest = repeat('b',64) WHERE id = :artifact", records)
    assert_rejected(connection, "UPDATE artifact_metadata SET storage_key = 'replacement' WHERE id = :artifact", records)


def test_state_change_audit_and_outbox_are_atomic(connection):
    records = seed_task(connection)
    old_count = connection.scalar(text("SELECT count(*) FROM outbox_events"))
    old_version = connection.scalar(text("SELECT version FROM tasks WHERE id = :task"), records)
    connection.execute(text("UPDATE tasks SET priority = priority + 1 WHERE id = :task"), records)
    assert connection.scalar(text("SELECT version FROM tasks WHERE id = :task"), records) == old_version + 1
    assert connection.scalar(text("SELECT count(*) FROM outbox_events")) == old_count + 1
    assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE entity_id = :task"), records) == 2
    assert connection.scalar(text("SELECT envelope ? 'event_id' FROM outbox_events WHERE entity_id = :task ORDER BY entity_version DESC LIMIT 1"), records)


def test_outbox_stale_token_cannot_acknowledge(connection):
    from sqlalchemy.orm import Session
    from hermes_coordination.storage.repositories import OutboxRepository

    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        repository = OutboxRepository(session)
        leased = repository.lease(limit=1)
        assert leased
        wrong_token = connection.scalar(text("SELECT gen_random_uuid()"))
        assert not repository.mark_published(leased[0].id, wrong_token)
        assert repository.mark_published(leased[0].id, leased[0].publisher_lease)
        session.commit()


def seed_approval(connection, records):
    assignment, attempt = seed_assignment(connection, records)
    user = connection.scalar(text("INSERT INTO principals(subject,kind) VALUES (gen_random_uuid()::text,'user') RETURNING id"))
    policy = connection.scalar(text("""
        INSERT INTO approval_policies(revision,action_category,scope_rules,required_approver_role,expiry_rules,
            policy_digest,activation_state)
        VALUES (1,'external_publication','{}','owner','{}',repeat('c',64),'active') RETURNING id
    """))
    approval = connection.scalar(text("""
        INSERT INTO approval_requests(task_id,attempt_id,policy_id,policy_revision,action_digest,action_specification,
            requesting_principal,expires_at)
        VALUES (:task,:attempt,:policy,1,repeat('d',64),'{"action":"publish"}',:actor,
            CURRENT_TIMESTAMP + interval '5 minutes') RETURNING id
    """), {**records, "attempt": attempt.id, "policy": policy})
    return {**records, "assignment": assignment, "attempt": attempt.id, "user": user,
            "policy": policy, "approval": approval}


def test_approval_requires_durable_unique_revision_decision(connection):
    records = seed_approval(connection, seed_task(connection))
    assert_rejected(connection, "UPDATE approval_requests SET state='approved' WHERE id=:approval", records)
    connection.execute(text("""
        INSERT INTO approval_decisions(request_id,request_revision,authenticated_user,decision,reason,idempotency_key)
        VALUES (:approval,1,:user,'approve','test authorization',gen_random_uuid()::text)
    """), records)
    assert_rejected(connection, """
        INSERT INTO approval_decisions(request_id,request_revision,authenticated_user,decision,reason,idempotency_key)
        VALUES (:approval,1,:user,'reject','conflicting',gen_random_uuid()::text)
    """, records, sqlstate="23505")
    connection.execute(text("UPDATE approval_requests SET state='approved' WHERE id=:approval"), records)
    assert_rejected(connection, "UPDATE approval_requests SET action_specification='{\"action\":\"delete\"}' WHERE id=:approval", records)
    assert_rejected(connection, """
        INSERT INTO approval_request_artifacts(request_id,artifact_id,checksum_digest)
        VALUES (:approval,:artifact,repeat('a',64))
    """, records)


def test_proposals_require_atomic_acceptance_and_freeze_payload(connection):
    records = seed_task(connection)
    assignment, attempt = seed_assignment(connection, records)
    proposal = connection.scalar(text("""
        INSERT INTO work_proposals(proposing_agent_id,proposing_instance_id,originating_task_id,originating_attempt_id,
            proposal_type,state,payload_json,payload_digest,expiry_at,submission_idempotency_key)
        VALUES (:agent,:instance,:task,:attempt,'follow_up_research','submitted','{}',repeat('a',64),
            CURRENT_TIMESTAMP + interval '5 minutes',gen_random_uuid()::text) RETURNING id
    """), {**records, "attempt": attempt.id})
    assert_rejected(connection, "UPDATE work_proposals SET payload_json=CAST(:payload AS jsonb) WHERE id=:proposal",
                    {"proposal": proposal, "payload": '{"changed":true}'})
    assert_rejected(connection, "UPDATE work_proposals SET state='accepted' WHERE id=:proposal", {"proposal": proposal})
    assert_rejected(connection, "INSERT INTO proposal_artifacts(proposal_id,artifact_id) VALUES (:proposal,:artifact)",
                    {**records, "proposal": proposal})