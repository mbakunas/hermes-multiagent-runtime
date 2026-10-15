from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from hermes_coordination.storage.models import Base
from hermes_coordination.storage.repositories import OutboxRepository
from test_postgresql_integrity import assert_rejected, seed_assignment, seed_task


def owned_write(connection, records, table, data):
    statement = text("SELECT public.hermes_write_owned_record(:table, :attempt, :token, :data)").bindparams(
        bindparam("data", type_=JSONB))
    return connection.scalar(statement, {**records, "table": table, "data": data})


def claim(connection, records):
    assert connection.scalar(text("SELECT public.hermes_claim_attempt(:attempt, :token, 60)"), records)


@pytest.fixture
def node_task(engine, services):
    with engine.begin() as connection:
        records = seed_task(connection, node=services["node_id"], create_instance=False)
    with services["admin"].begin() as connection:
        connection.execute(text("INSERT INTO agent_placements(agent_id,node_id) VALUES (:agent,:node)"), records)
    with services["node"].begin() as connection:
        records["instance"] = connection.scalar(text(
            "SELECT public.hermes_register_instance(:agent,'pinned','verified')"), records)
        assert connection.scalar(text("SELECT public.hermes_set_instance_state(:instance,'ready','ready')"), records)
    with services["orchestrator"].begin() as connection:
        assignment, attempt = seed_assignment(connection, {**records, "actor": services["actors"]["orchestrator"]})
    return {**records, "assignment": assignment, "attempt": attempt.id, "token": attempt.ownership_token}


@pytest.fixture
def approved_continuation(engine, services, node_task):
    records = node_task.copy()
    with engine.begin() as connection:
        records["user"] = connection.scalar(text(
            "INSERT INTO principals(subject,kind) VALUES (gen_random_uuid()::text,'user') RETURNING id"))
        records["policy"] = connection.scalar(text("""
            INSERT INTO approval_policies(revision,action_category,scope_rules,required_approver_role,
                expiry_rules,policy_digest,activation_state)
            VALUES (1,gen_random_uuid()::text,'{}','owner','{}',repeat('c',64),'active') RETURNING id
        """))
    with services["node"].begin() as connection:
        claim(connection, records)
        owned_write(connection, records, "execution_runs", {
            "node_execution_id": str(uuid4()), "adapter_version": "v1"})
        assert connection.scalar(text("""
            SELECT public.hermes_finish_execution(:attempt,:token,'session','interrupted','{}')
        """), records)
        records["approval"] = owned_write(connection, records, "approval_requests", {
            "policy_id": str(records["policy"]), "policy_revision": 1, "ttl_seconds": 300,
            "action_specification": {"action": "publish", "destination": "reviewed-release"}})
    records["requested_attempt"] = records["attempt"]
    records["requested_token"] = records["token"]
    with services["approval"].begin() as connection:
        assert connection.scalar(text("SELECT state FROM task_attempts WHERE id=:attempt"), records) == "waiting_approval"
        connection.execute(text("""
            INSERT INTO approval_decisions(request_id,request_revision,authenticated_user,decision,reason,idempotency_key)
            VALUES (:approval,1,:user,'approve','reviewed release',gen_random_uuid()::text)
        """), records)
        connection.execute(text("UPDATE approval_requests SET state='approved' WHERE id=:approval"), records)
    with services["orchestrator"].begin() as connection:
        connection.execute(text("UPDATE tasks SET state='ready' WHERE id=:task"), records)
        assignment, attempt = seed_assignment(connection, {**records, "actor": services["actors"]["orchestrator"]},
                                                attempt_number=2)
        records.update(assignment=assignment, attempt=attempt.id, token=attempt.ownership_token)
        records["digest"] = connection.scalar(text("SELECT action_digest FROM approval_requests WHERE id=:approval"), records)
    with services["node"].begin() as connection:
        claim(connection, records)
    return records


def test_real_login_roles_are_distinct_and_unprivileged(services):
    logins = set()
    for service in ("node", "intake", "approval", "outbox", "orchestrator", "admin"):
        with services[service].connect() as connection:
            login = connection.scalar(text("SELECT session_user"))
            logins.add(login)
            assert login == services[service].url.username
            assert connection.scalar(text("SELECT pg_has_role(session_user,:group,'MEMBER')"), {"group": f"hermes_{service}"})
            assert not connection.scalar(text("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=session_user"))
            for other in ("node", "intake", "approval", "outbox", "orchestrator", "admin"):
                if other != service:
                    assert not connection.scalar(text("SELECT pg_has_role(session_user,:group,'MEMBER')"), {"group": f"hermes_{other}"})
    assert len(logins) == 6


def test_live_schema_matches_model_keys_and_foreign_keys(engine):
    with engine.begin() as connection:
        records = seed_task(connection)
        event = connection.scalar(text("SELECT id FROM outbox_events WHERE entity_id=:task"), records)
        connection.execute(text("UPDATE outbox_events SET publication_status='published' WHERE id=:event"), {"event": event})
    with engine.connect() as connection:
        catalog = inspect(connection)
        for name, table in Base.metadata.tables.items():
            assert {column["name"] for column in catalog.get_columns(name)} == set(table.c.keys())
            assert catalog.get_pk_constraint(name)["constrained_columns"] == [column.name for column in table.primary_key]
            actual = {(tuple(edge["constrained_columns"]), edge["referred_table"], tuple(edge["referred_columns"]))
                      for edge in catalog.get_foreign_keys(name)}
            expected = {(tuple(edge.parent.name for edge in constraint.elements),
                         constraint.elements[0].column.table.name,
                         tuple(edge.column.name for edge in constraint.elements)) for constraint in table.foreign_key_constraints}
            assert actual == expected, name
        assert connection.scalar(text("""
            SELECT count(*) FROM pg_partitioned_table WHERE partrelid IN
                ('audit_events'::regclass,'task_events'::regclass,'outbox_events'::regclass) AND partstrat='r'
        """)) == 3
        assert connection.scalar(text("""
            SELECT count(*) FROM audit_events WHERE action='publication.changed' AND entity_type <> 'outbox_events'
        """)) > 0
        assert connection.scalar(text("""
            SELECT count(*) FROM audit_events WHERE action='publication.changed' AND NOT (payload ? 'event_id')
        """)) == 0
        assert connection.scalar(text("""
            SELECT count(*) FROM coordination_entities WHERE entity_type IN ('audit_events','task_events','outbox_events')
        """)) == 0


def test_intake_login_creates_artifact_and_request(services):
    with services["intake"].begin() as connection:
        artifact = connection.scalar(text("""
            INSERT INTO artifact_metadata(uploader_identity,artifact_kind,media_type,byte_size,checksum_digest,
                export_identifier,environment,storage_key,original_filename,provenance,retention_policy,state)
            VALUES (:actor,'markdown','text/markdown',0,repeat('a',64),'intake','test',gen_random_uuid()::text,
                'intent.md','{"origin":"external_intake"}','{}','verified') RETURNING id
        """), {"actor": services["actors"]["intake"]})
        assert connection.scalar(text("""
            INSERT INTO requests(authenticated_initiator,intent_reference,idempotency_key)
            VALUES (:actor,:artifact,gen_random_uuid()::text) RETURNING id
        """), {"actor": services["actors"]["intake"], "artifact": artifact})
        assert_rejected(connection, "UPDATE tasks SET priority=1", sqlstate="42501")


def test_node_execution_artifacts_proposals_and_completion(services, node_task):
    records = node_task
    with services["node"].begin() as connection:
        claim(connection, records)
        owned_write(connection, records, "execution_runs", {
            "node_execution_id": str(uuid4()), "adapter_version": "v1"})
        artifact = owned_write(connection, records, "artifact_metadata", {
            "artifact_kind": "markdown", "media_type": "text/markdown", "byte_size": 0,
            "checksum_digest": "a" * 64, "export_identifier": "node", "environment": "test",
            "storage_key": str(uuid4()), "original_filename": "result.md", "provenance": {}, "retention_policy": {}})
        assert connection.scalar(text("SELECT public.hermes_accept_artifact(:artifact,:attempt,:token)"),
                                 {**records, "artifact": artifact})
        proposal = owned_write(connection, records, "work_proposals", {
            "proposal_type": "follow_up_research", "payload_json": {"topic": "follow-up"},
            "ttl_seconds": 300, "submission_idempotency_key": str(uuid4()), "artifact_ids": [str(artifact)]})
        assert connection.scalar(text("SELECT state FROM work_proposals WHERE id=:id"), {"id": proposal}) == "submitted"
        assert connection.scalar(text("SELECT public.hermes_finish_execution(:attempt,:token,'session','succeeded','{}')"), records)
        result = connection.scalar(text("""
            SELECT public.hermes_complete_attempt(:attempt,:token,'succeeded','finished','{}',ARRAY[CAST(:artifact AS uuid)],NULL)
        """), {**records, "artifact": artifact})
        assert connection.scalar(text("SELECT artifact_id FROM task_result_artifacts WHERE result_id=:id"), {"id": result}) == artifact
    with services["node"].begin() as connection:
        assert connection.scalar(text("SELECT state FROM tasks WHERE id=:task"), records) == "succeeded"
        assert connection.scalar(text("SELECT state FROM assignments WHERE id=:assignment"), records) == "completed"
        assert connection.scalar(text("SELECT public.hermes_set_instance_state(:instance,'draining','degraded')"), records)
        assert connection.scalar(text("SELECT public.hermes_set_instance_state(:instance,'stopped','not_ready')"), records)
        assert_rejected(connection, "SELECT public.hermes_renew_attempt(:attempt,:token,60)", records, sqlstate="42501")


def test_request_decide_continue_record_and_dispatch(services, approved_continuation):
    records = approved_continuation
    with services["node"].begin() as connection:
        effect = owned_write(connection, records, "effect_intents", {
            "approval_request_id": str(records["approval"]), "business_idempotency_key": str(uuid4())})
        assert connection.scalar(text("SELECT public.hermes_set_effect_status(:effect,:attempt,:token,'dispatched','external-1')"),
                                 {**records, "effect": effect})
        assert connection.scalar(text("SELECT attempt_id FROM approval_requests WHERE id=:approval"), records) == records["requested_attempt"]
        row = connection.execute(text("SELECT attempt_id,execution_status,action_digest FROM effect_intents WHERE id=:effect"),
                                 {"effect": effect}).one()
        assert row.attempt_id == records["attempt"] != records["requested_attempt"]
        assert row.execution_status == "dispatched"
        assert row.action_digest == records["digest"]
        assert connection.scalar(text("SELECT public.hermes_set_effect_status(:effect,:attempt,:token,'succeeded','external-1')"),
                                 {**records, "effect": effect})


def test_continuation_approval_still_binds_task_and_digest(engine, services, approved_continuation):
    records = approved_continuation
    with services["orchestrator"].begin() as connection:
        assert_rejected(connection, """
            INSERT INTO effect_intents(task_id,attempt_id,action_digest,approval_request_id,business_idempotency_key)
            VALUES (:task,:attempt,repeat('f',64),:approval,gen_random_uuid()::text)
        """, records, sqlstate="23503")
    with services["node"].begin() as connection:
        assert_rejected(connection, "SELECT public.hermes_renew_attempt(:requested_attempt,:requested_token,60)", records, sqlstate="42501")
    with engine.begin() as connection:
        foreign = seed_task(connection, node=services["node_id"])
    with services["orchestrator"].begin() as connection:
        _, attempt = seed_assignment(connection, {**foreign, "actor": services["actors"]["orchestrator"]})
        foreign.update(attempt=attempt.id, token=attempt.ownership_token, approval=records["approval"])
    with services["node"].begin() as connection:
        claim(connection, foreign)
        assert_rejected(connection, """
            SELECT public.hermes_write_owned_record('effect_intents',:attempt,:token,
                jsonb_build_object('approval_request_id',CAST(:approval AS text),'business_idempotency_key',gen_random_uuid()::text))
        """, foreign)
    with services["approval"].begin() as connection:
        connection.execute(text("UPDATE approval_requests SET state='revoked' WHERE id=:approval"), records)
    with services["node"].begin() as connection:
        assert_rejected(connection, """
            SELECT public.hermes_write_owned_record('effect_intents',:attempt,:token,
                jsonb_build_object('approval_request_id',CAST(:approval AS text),'business_idempotency_key',gen_random_uuid()::text))
        """, records)


def event_counts(connection):
    return connection.execute(text("""
        SELECT (SELECT count(*) FROM audit_events), (SELECT count(*) FROM outbox_events),
            (SELECT count(*) FROM coordination_entities)
    """)).one()


def test_heartbeat_and_lease_renewal_do_not_audit_or_publish(engine, services, node_task):
    with services["node"].begin() as connection:
        claim(connection, node_task)
    with engine.connect() as connection:
        before = event_counts(connection)
        versions = connection.execute(text("SELECT id,version FROM coordination_entities WHERE id IN (:node,:instance,:attempt) ORDER BY id"), node_task).all()
    with services["node"].begin() as connection:
        assert connection.scalar(text("SELECT public.hermes_heartbeat(:instance)"), node_task)
        assert connection.scalar(text("SELECT public.hermes_heartbeat()"))
        assert connection.scalar(text("SELECT public.hermes_renew_attempt(:attempt,:token,120)"), node_task)
        assert connection.scalar(text("SELECT last_heartbeat_at IS NOT NULL FROM nodes WHERE id=:node"), node_task)
        assert connection.scalar(text("SELECT last_heartbeat_at IS NOT NULL FROM agent_instances WHERE id=:instance"), node_task)
        assert connection.scalar(text("SELECT lease_expires_at > clock_timestamp() + interval '60 seconds' FROM task_attempts WHERE id=:attempt"), node_task)
    with engine.connect() as connection:
        assert event_counts(connection) == before
        assert connection.execute(text("SELECT id,version FROM coordination_entities WHERE id IN (:node,:instance,:attempt) ORDER BY id"), node_task).all() == versions


@pytest.mark.parametrize("placement", ["absent", "other_node", "disabled"])
def test_digest_cannot_authorize_unplaced_agent(engine, services, placement):
    with engine.begin() as connection:
        records = seed_task(connection, node=services["node_id"], create_instance=False)
        other_node = seed_task(connection)["node"] if placement == "other_node" else records["node"]
    if placement != "absent":
        with services["admin"].begin() as connection:
            connection.execute(text("INSERT INTO agent_placements(agent_id,node_id,enabled) VALUES (:agent,:node,:enabled)"),
                               {**records, "node": other_node, "enabled": placement != "disabled"})
    with services["node"].begin() as connection:
        assert_rejected(connection, "SELECT public.hermes_register_instance(:agent,'pinned','verified')", records, sqlstate="42501")
        assert_rejected(connection, "INSERT INTO agent_placements(agent_id,node_id) VALUES (:agent,:node)", records, sqlstate="42501")


def test_placement_administration_is_audited_and_digest_still_checked(engine, services):
    with engine.begin() as connection:
        records = seed_task(connection, node=services["node_id"], create_instance=False)
    with services["admin"].begin() as connection:
        placement = connection.scalar(text("INSERT INTO agent_placements(agent_id,node_id) VALUES (:agent,:node) RETURNING id"), records)
        connection.execute(text("UPDATE agent_placements SET enabled=false WHERE id=:id"), {"id": placement})
        assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE entity_id=:id"), {"id": placement}) == 2
        connection.execute(text("UPDATE agent_placements SET enabled=true WHERE id=:id"), {"id": placement})
    with services["node"].begin() as connection:
        assert_rejected(connection, "SELECT public.hermes_register_instance(:agent,'pinned','wrong-digest')", records)
        assert connection.scalar(text("SELECT public.hermes_register_instance(:agent,'pinned','verified')"), records)


def test_archival_is_privileged_audited_and_preserves_unpublished(engine, services):
    with engine.begin() as connection:
        records = seed_task(connection)
        connection.execute(text("UPDATE tasks SET priority=priority+1 WHERE id=:task"), records)
        events = connection.execute(text("SELECT id FROM outbox_events WHERE entity_id=:task ORDER BY entity_version"), records).scalars().all()
    with services["outbox"].begin() as connection:
        connection.execute(text("UPDATE outbox_events SET publication_status='published' WHERE id=:id"), {"id": events[0]})
    with engine.connect() as connection:
        cutoff = connection.scalar(text("SELECT clock_timestamp()"))
    with services["node"].begin() as connection:
        assert_rejected(connection, "SELECT public.hermes_archive_events(:cutoff,'retention')", {"cutoff": cutoff}, sqlstate="42501")
    with services["admin"].begin() as connection:
        connection.exec_driver_sql("SET LOCAL hermes.archiving='on'")
        assert_rejected(connection, "DELETE FROM audit_events", sqlstate="42501")
        assert_rejected(connection, "TRUNCATE audit_events", sqlstate="42501")
        assert_rejected(connection, "SELECT public.hermes_archive_events(NULL,'')", sqlstate="22023")
        with connection.begin_nested() as savepoint:
            probe = connection.scalar(text("SELECT public.hermes_archive_events(:cutoff,'rollback probe')"), {"cutoff": cutoff})
            assert probe["outbox_events"] >= 1
            savepoint.rollback()
        assert connection.scalar(text("SELECT EXISTS(SELECT 1 FROM outbox_events WHERE id=:id)"), {"id": events[0]})
        assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE payload->>'reason'='rollback probe'")) == 0
        deleted = connection.scalar(text("SELECT public.hermes_archive_events(:cutoff,'validated export retained elsewhere')"), {"cutoff": cutoff})
        assert deleted["outbox_events"] >= 1
        assert deleted["audit_events"] >= 1
        assert deleted["task_events"] >= 1
        assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM outbox_events WHERE id=:id)"), {"id": events[0]})
        assert connection.scalar(text("SELECT publication_status FROM outbox_events WHERE id=:id"), {"id": events[1]}) == "pending"
        audit = connection.execute(text("SELECT actor,payload FROM audit_events WHERE action='events.archived' ORDER BY created_at DESC LIMIT 1")).one()
        assert audit.actor == services["actors"]["admin"]
        assert audit.payload["deleted"] == deleted
        assert audit.payload["reason"] == "validated export retained elsewhere"
        assert_rejected(connection, "UPDATE audit_events SET action='rewritten'", sqlstate="42501")


def test_outbox_lease_only_changes_do_not_generate_audits(engine, services):
    with engine.begin() as connection:
        seed_task(connection)
    with Session(services["outbox"]) as session:
        repository = OutboxRepository(session)
        leased = repository.lease(limit=1)
        assert leased
        event = leased[0]
        event_id, token = event.id, event.publisher_lease
        session.commit()
    with engine.connect() as connection:
        before = event_counts(connection)
    with services["outbox"].begin() as connection:
        connection.execute(text("UPDATE outbox_events SET publisher_lease_expires_at=clock_timestamp()+interval '60 seconds' WHERE id=:id"), {"id": event_id})
    with engine.connect() as connection:
        assert event_counts(connection) == before
    with Session(services["outbox"]) as session:
        repository = OutboxRepository(session)
        assert not repository.mark_published(event_id, uuid4())
        assert repository.retry(event_id, token, 0)
        event = repository.lease(limit=1)[0]
        assert repository.mark_published(event.id, event.publisher_lease)
        session.commit()


def test_two_publishers_lease_disjoint_batches(engine, services):
    with engine.begin() as connection:
        records = seed_task(connection)
        tasks = [connection.scalar(text("""
            INSERT INTO tasks(workflow_id,specification_revision,output_contract)
            VALUES (:workflow,'publisher-test','{}') RETURNING id
        """), records) for _ in range(10)]
        expected = set(connection.scalars(text("SELECT id FROM outbox_events WHERE entity_id=ANY(:tasks)"), {"tasks": tasks}))
    assert len(expected) == 10
    with services["outbox"].begin() as connection:
        connection.execute(text("""
            UPDATE outbox_events SET retry_at=clock_timestamp()+interval '1 day' WHERE id<>ALL(:events)
        """), {"events": list(expected)})
    barrier = Barrier(2)

    def publish():
        with Session(services["outbox"]) as session:
            repository = OutboxRepository(session)
            batch = repository.lease(limit=5)
            barrier.wait(timeout=10)
            identifiers = {event.id for event in batch}
            for event in batch:
                assert repository.mark_published(event.id, event.publisher_lease)
            session.commit()
            return identifiers

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish) for _ in range(2)]
        first, second = [future.result(timeout=20) for future in futures]
    assert len(first) == len(second) == 5
    assert first.isdisjoint(second)
    assert first | second == expected


def test_concurrent_double_assignment(engine, services):
    with engine.begin() as connection:
        records = seed_task(connection)
    records["actor"] = services["actors"]["orchestrator"]
    barrier = Barrier(2)

    def assign():
        try:
            with services["orchestrator"].begin() as connection:
                barrier.wait(timeout=10)
                seed_assignment(connection, records)
            return "committed"
        except DBAPIError as error:
            return error.orig.sqlstate

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(assign) for _ in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == ["P0001", "committed"]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM assignments WHERE task_id=:task AND state='active'"), records) == 1


def wait_for_database_lock(engine, backend):
    deadline = monotonic() + 10
    pause = Event()
    with engine.connect() as connection:
        while monotonic() < deadline:
            if connection.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": backend}):
                return
            connection.exec_driver_sql("SELECT pg_stat_clear_snapshot()")
            pause.wait(0.01)
    pytest.fail("Concurrent backend never reached the expected database lock")


def test_lease_expiry_racing_completion(engine, services, node_task):
    with services["node"].begin() as connection:
        claim(connection, node_task)
    backends = Queue()

    def complete():
        try:
            with services["node"].begin() as connection:
                backends.put(connection.scalar(text("SELECT pg_backend_pid()")))
                connection.execute(text("SELECT public.hermes_complete_attempt(:attempt,:token,'succeeded','late','{}',ARRAY[]::uuid[],NULL)"), node_task)
            return "committed"
        except DBAPIError as error:
            return error.orig.sqlstate

    with ThreadPoolExecutor(max_workers=1) as executor:
        with services["orchestrator"].begin() as connection:
            connection.execute(text("SELECT id FROM tasks WHERE id=:task FOR UPDATE"), node_task)
            connection.execute(text("UPDATE task_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:attempt"), node_task)
            future = executor.submit(complete)
            wait_for_database_lock(services["node"], backends.get(timeout=10))
        assert future.result(timeout=20) == "42501"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM task_results WHERE attempt_id=:attempt"), node_task) == 0


def test_concurrent_dependency_cycles(engine, services):
    with engine.begin() as connection:
        records = seed_task(connection)
        records["other"] = connection.scalar(text("""
            INSERT INTO tasks(workflow_id,specification_revision,output_contract) VALUES (:workflow,'v1','{}') RETURNING id
        """), records)
    barrier = Barrier(2)

    def add_edge(reverse):
        try:
            with services["orchestrator"].begin() as connection:
                barrier.wait(timeout=10)
                connection.execute(text("INSERT INTO task_dependencies(prerequisite_task_id,dependent_task_id) VALUES (:source,:target)"),
                                   {"source": records["other"] if reverse else records["task"],
                                    "target": records["task"] if reverse else records["other"]})
            return "committed"
        except DBAPIError as error:
            return error.orig.sqlstate

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(add_edge, reverse) for reverse in (False, True)]
        assert sorted(future.result(timeout=20) for future in futures) == ["P0001", "committed"]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM task_dependencies WHERE prerequisite_task_id IN (:task,:other)"), records) == 1