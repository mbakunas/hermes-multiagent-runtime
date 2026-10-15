"""Explicit security functions and bounded event maintenance.

Revision ID: 0003_review_followup
Revises: 0002_critical_review
"""

from alembic import op


revision = "0003_review_followup"
down_revision = "0002_critical_review"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.create_unique_constraint("uq_effect_intents_approval_request_id", "effect_intents", ["approval_request_id"])
    op.execute(GUARD_RECORD)
    op.execute(AUDIT_CHANGE)
    op.execute(WRITE_OWNED_RECORD)
    op.execute(REGISTER_INSTANCE)
    op.execute(EVENT_MAINTENANCE)
    op.execute("SET CONSTRAINTS ALL DEFERRED")


def downgrade():
    raise RuntimeError("Restore a verified backup into a separate database instead of destructive downgrade")


GUARD_RECORD = r"""
CREATE OR REPLACE FUNCTION public.hermes_guard_record() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    record_table text := (SELECT relname FROM pg_class WHERE oid = COALESCE(pg_partition_root(TG_RELID), TG_RELID));
    previous jsonb;
    proposed jsonb;
    field_name text;
    is_orchestrator boolean := pg_has_role(session_user, 'hermes_orchestrator', 'MEMBER');
    approval public.approval_requests%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF record_table IN ('audit_events','task_events','outbox_events')
           AND pg_has_role(session_user, 'hermes_admin', 'MEMBER')
           AND current_setting('hermes.archiving', true) = 'on' THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Coordination history cannot be deleted' USING ERRCODE = '42501';
    END IF;
    proposed := to_jsonb(NEW);
    IF TG_OP = 'UPDATE' THEN
        previous := to_jsonb(OLD);
        IF NEW.id <> OLD.id THEN RAISE EXCEPTION 'Primary keys are immutable'; END IF;
        IF record_table IN ('principals','capability_definitions','instance_capabilities','task_input_references',
            'task_capabilities','task_dependencies','proposal_tasks','proposal_artifacts','proposal_decisions',
            'approval_decisions','task_results','task_result_artifacts','approval_request_artifacts',
            'artifact_lineage','audit_events','task_events') THEN
            RAISE EXCEPTION 'Record is append-only: %', record_table;
        END IF;
        IF record_table = 'coordination_entities' AND
           ((proposed - 'version') IS DISTINCT FROM (previous - 'version') OR
            (proposed->>'version')::integer <> (previous->>'version')::integer + 1) THEN
            RAISE EXCEPTION 'Entity identity/type is immutable';
        END IF;
        IF record_table IN ('tasks','workflows') THEN
            IF NEW.version NOT IN (OLD.version, OLD.version + 1) THEN RAISE EXCEPTION 'Invalid version increment'; END IF;
            proposed := jsonb_set(proposed, '{version}', to_jsonb(OLD.version + 1));
        END IF;
        IF record_table = 'tasks' AND NOT is_orchestrator THEN
            IF (proposed - ARRAY['state','current_attempt_id','version','updated_at']) IS DISTINCT FROM
               (previous - ARRAY['state','current_attempt_id','version','updated_at']) THEN
                RAISE EXCEPTION 'Only Orchestrator may change task specification or scheduling';
            END IF;
            IF NOT ((previous->>'state' = 'assigned' AND proposed->>'state' = 'running') OR
                (previous->>'state' = 'running' AND proposed->>'state' IN ('succeeded','failed','cancelled','waiting_approval'))) THEN
                RAISE EXCEPTION 'Node cannot schedule work';
            END IF;
        END IF;
        IF record_table = 'agent_instances' AND
           (NEW.agent_id IS DISTINCT FROM OLD.agent_id OR NEW.node_id IS DISTINCT FROM OLD.node_id) THEN
            RAISE EXCEPTION 'Instance agent/node identity is immutable';
        END IF;
        IF record_table = 'agent_placements' AND
           (proposed - ARRAY['enabled','updated_at']) IS DISTINCT FROM (previous - ARRAY['enabled','updated_at']) THEN
            RAISE EXCEPTION 'Placement identity is immutable';
        END IF;
        IF record_table = 'task_attempts' THEN
            IF NEW.ownership_token IS DISTINCT FROM OLD.ownership_token THEN
                RAISE EXCEPTION 'Attempt ownership token is immutable';
            END IF;
            IF NOT ((OLD.state = 'assigned' AND NEW.state IN ('assigned','running','cancelled','expired')) OR
                    (OLD.state = 'running' AND NEW.state IN ('running','succeeded','failed','cancelled','expired','waiting_approval'))) THEN
                RAISE EXCEPTION 'Terminal attempt history is immutable';
            END IF;
            IF (proposed - ARRAY['state','lease_expires_at','started_at','ended_at','error_classification','updated_at'])
               IS DISTINCT FROM (previous - ARRAY['state','lease_expires_at','started_at','ended_at','error_classification','updated_at']) THEN
                RAISE EXCEPTION 'Attempt assignment/identity is immutable';
            END IF;
        END IF;
        IF record_table = 'artifact_metadata' AND previous->>'state' IN ('accepted','retired','deletion_pending','deleted') THEN
            IF (proposed - ARRAY['state','retention_policy','retention_expires_at','retention_hold','updated_at'])
               IS DISTINCT FROM (previous - ARRAY['state','retention_policy','retention_expires_at','retention_hold','updated_at']) THEN
                RAISE EXCEPTION 'Accepted artifact content/provenance is immutable';
            END IF;
            IF NOT ((OLD.state = 'accepted' AND NEW.state IN ('accepted','retired')) OR
                    (OLD.state = 'retired' AND NEW.state IN ('retired','deletion_pending')) OR
                    (OLD.state = 'deletion_pending' AND NEW.state IN ('deletion_pending','deleted'))) THEN
                RAISE EXCEPTION 'Invalid accepted artifact transition';
            END IF;
        END IF;
        IF record_table = 'work_proposals' THEN
            IF OLD.state <> 'draft' AND
               (proposed - ARRAY['state','updated_at']) IS DISTINCT FROM (previous - ARRAY['state','updated_at']) THEN
                RAISE EXCEPTION 'Submitted proposal is immutable';
            END IF;
            IF NOT ((OLD.state = 'draft' AND NEW.state IN ('draft','submitted')) OR
                    (OLD.state = 'submitted' AND NEW.state IN ('accepted','rejected','expired'))) THEN
                RAISE EXCEPTION 'Invalid proposal transition';
            END IF;
        END IF;
        IF record_table = 'approval_requests' THEN
            IF (proposed - ARRAY['state','updated_at']) IS DISTINCT FROM (previous - ARRAY['state','updated_at']) THEN
                RAISE EXCEPTION 'Approval action binding is immutable; create a new request';
            END IF;
            IF NOT ((OLD.state = 'pending' AND NEW.state IN ('approved','rejected','expired','cancelled')) OR
                    (OLD.state = 'approved' AND NEW.state = 'revoked')) THEN
                RAISE EXCEPTION 'Invalid approval transition';
            END IF;
        END IF;
        IF record_table = 'approval_policies' AND
           ((proposed - 'activation_state') IS DISTINCT FROM (previous - 'activation_state') OR
            previous->>'activation_state' <> 'active' OR proposed->>'activation_state' <> 'retired') THEN
            RAISE EXCEPTION 'Policy content is immutable; only retirement is allowed';
        END IF;
        IF record_table = 'outbox_events' AND
           ((proposed - ARRAY['publication_status','publisher_lease','publisher_lease_expires_at','retry_at'])
            IS DISTINCT FROM (previous - ARRAY['publication_status','publisher_lease','publisher_lease_expires_at','retry_at'])
            OR previous->>'publication_status' = 'published') THEN
            RAISE EXCEPTION 'Outbox event content is immutable';
        END IF;
    END IF;
    IF record_table IN ('proposal_tasks','proposal_decisions') AND NOT is_orchestrator THEN
        RAISE EXCEPTION 'Orchestrator is the sole assignment authority' USING ERRCODE = '42501';
    END IF;
    IF record_table = 'assignments' THEN
        IF NOT is_orchestrator AND NOT (
            TG_OP = 'UPDATE' AND pg_has_role(session_user, 'hermes_node', 'MEMBER')
            AND previous->>'state' = 'active' AND proposed->>'state' = 'completed'
            AND (proposed - ARRAY['state','updated_at']) IS NOT DISTINCT FROM (previous - ARRAY['state','updated_at'])
        ) THEN RAISE EXCEPTION 'Orchestrator is the sole assignment authority' USING ERRCODE = '42501'; END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.authoritative_assigner <> public.hermes_actor() THEN
                RAISE EXCEPTION 'Assignment principal does not match authenticated service';
            END IF;
            PERFORM 1 FROM public.tasks WHERE id = NEW.task_id AND state = 'ready' AND NOT cancellation_intent FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Task is not assignable'; END IF;
            PERFORM 1 FROM public.agent_instances instance
                JOIN public.agents agent ON agent.id = instance.agent_id
                JOIN public.nodes node ON node.id = instance.node_id
                JOIN public.agent_placements placement ON placement.agent_id = instance.agent_id
                    AND placement.node_id = instance.node_id AND placement.enabled
                WHERE instance.id = NEW.instance_id AND instance.state = 'ready' AND instance.readiness = 'ready'
                    AND agent.desired_state = 'enabled' AND node.desired_state = 'enabled'
                    AND instance.verified_profile_digest = agent.approved_profile_revision
                FOR SHARE OF placement;
            IF NOT FOUND THEN RAISE EXCEPTION 'Instance is not eligible'; END IF;
            IF EXISTS (SELECT 1 FROM public.task_capabilities requirement WHERE requirement.task_id = NEW.task_id
                AND requirement.requirement_kind = 'required' AND NOT EXISTS (
                    SELECT 1 FROM public.instance_capabilities inventory
                    WHERE inventory.instance_id = NEW.instance_id AND inventory.capability_id = requirement.capability_id
                )) THEN RAISE EXCEPTION 'Required capability not registered'; END IF;
        END IF;
    END IF;
    IF record_table = 'proposal_decisions' THEN
        IF NEW.orchestrator_principal <> public.hermes_actor() THEN RAISE EXCEPTION 'Proposal decision principal mismatch'; END IF;
    END IF;
    IF record_table = 'requests' THEN
        IF NEW.concierge_reference IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.agents WHERE id = NEW.concierge_reference AND role = 'concierge'
        ) THEN RAISE EXCEPTION 'Concierge reference must identify a Concierge'; END IF;
    END IF;
    IF record_table = 'task_dependencies' THEN
        PERFORM 1 FROM public.workflows WHERE id = (SELECT workflow_id FROM public.tasks WHERE id = NEW.dependent_task_id) FOR UPDATE;
        IF (SELECT workflow_id FROM public.tasks WHERE id = NEW.prerequisite_task_id)
           IS DISTINCT FROM (SELECT workflow_id FROM public.tasks WHERE id = NEW.dependent_task_id) THEN
            RAISE EXCEPTION 'Dependencies must remain in one workflow';
        END IF;
        IF EXISTS (
            WITH RECURSIVE descendants(id) AS (
                SELECT NEW.dependent_task_id UNION
                SELECT edge.dependent_task_id FROM public.task_dependencies edge JOIN descendants ON edge.prerequisite_task_id = descendants.id
            ) SELECT 1 FROM descendants WHERE id = NEW.prerequisite_task_id
        ) THEN RAISE EXCEPTION 'Dependency cycle'; END IF;
    END IF;
    IF record_table = 'work_proposals' THEN
        IF NOT EXISTS (SELECT 1 FROM public.task_attempts attempt JOIN public.assignments assignment ON assignment.id = attempt.assignment_id
            WHERE attempt.id = NEW.originating_attempt_id AND assignment.instance_id = NEW.proposing_instance_id) THEN
            RAISE EXCEPTION 'Proposal origin does not match assigned instance';
        END IF;
        NEW.payload_digest := encode(public.digest(NEW.payload_json::text, 'sha256'), 'hex');
        proposed := to_jsonb(NEW);
    END IF;
    IF record_table = 'proposal_artifacts' THEN
        PERFORM 1 FROM public.work_proposals WHERE id = NEW.proposal_id AND state = 'draft' FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Submitted proposal artifact references are frozen'; END IF;
    END IF;
    IF record_table = 'approval_request_artifacts' THEN
        PERFORM 1 FROM public.approval_requests WHERE id = NEW.request_id AND state = 'pending' FOR UPDATE;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM public.approval_decisions WHERE request_id = NEW.request_id) THEN
            RAISE EXCEPTION 'Decided approval artifact bindings are frozen';
        END IF;
    END IF;
    IF record_table = 'approval_requests' THEN
        IF TG_OP = 'INSERT' OR NEW.state = 'approved' THEN
            PERFORM 1 FROM public.approval_policies WHERE id = NEW.policy_id AND revision = NEW.policy_revision
                AND activation_state = 'active' FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Approval requires an active policy revision'; END IF;
        END IF;
        NEW.action_digest := encode(public.digest(NEW.action_specification::text, 'sha256'), 'hex');
        proposed := to_jsonb(NEW);
        IF NEW.state IN ('approved','rejected') AND NOT EXISTS (
            SELECT 1 FROM public.approval_decisions WHERE request_id = NEW.id AND request_revision = NEW.revision
                AND decision::text = CASE NEW.state WHEN 'approved' THEN 'approve' ELSE 'reject' END
        ) THEN RAISE EXCEPTION 'Approval state requires a durable matching user decision'; END IF;
        IF NEW.state = 'approved' AND NEW.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'Approval expired'; END IF;
    END IF;
    IF record_table = 'approval_decisions' THEN
        SELECT * INTO approval FROM public.approval_requests WHERE id = NEW.request_id FOR UPDATE;
        IF approval.state <> 'pending' OR approval.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'Approval is not pending and valid'; END IF;
        IF NOT EXISTS (SELECT 1 FROM public.principals WHERE id = NEW.authenticated_user AND kind = 'user') THEN
            RAISE EXCEPTION 'An approval requires a registered human principal';
        END IF;
    END IF;
    IF record_table = 'effect_intents' THEN
        SELECT * INTO approval FROM public.approval_requests WHERE id = NEW.approval_request_id FOR UPDATE;
        IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.execution_status = 'dispatched' AND OLD.execution_status = 'prepared') THEN
            PERFORM 1 FROM public.approval_policies WHERE id = approval.policy_id AND activation_state = 'active' FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Effect policy revision is no longer active'; END IF;
            IF approval.state <> 'approved' OR approval.expires_at <= clock_timestamp() THEN
                RAISE EXCEPTION 'Sensitive effect requires a current durable approval';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM public.task_attempts attempt JOIN public.tasks task ON task.current_attempt_id = attempt.id
                WHERE attempt.id = NEW.attempt_id AND attempt.state = 'running' AND attempt.lease_expires_at > clock_timestamp()) THEN
                RAISE EXCEPTION 'Sensitive effect requires live attempt ownership';
            END IF;
        END IF;
        IF TG_OP = 'UPDATE' AND
           (proposed - ARRAY['execution_status','external_reference','updated_at']) IS DISTINCT FROM
           (previous - ARRAY['execution_status','external_reference','updated_at']) THEN RAISE EXCEPTION 'Effect action binding is immutable'; END IF;
        IF TG_OP = 'UPDATE' AND NOT (
            (OLD.execution_status = 'prepared' AND NEW.execution_status IN ('dispatched','cancelled')) OR
            (OLD.execution_status = 'dispatched' AND NEW.execution_status IN ('succeeded','failed','unknown')) OR
            (OLD.execution_status = 'unknown' AND NEW.execution_status IN ('succeeded','failed','cancelled'))
        ) THEN RAISE EXCEPTION 'Invalid effect transition; uncertain effects cannot be redispatched'; END IF;
    END IF;
    IF record_table = 'outbox_events' AND TG_OP = 'INSERT' THEN
        NEW.envelope := NEW.envelope || jsonb_build_object('event_id', NEW.id, 'event_type', NEW.event_type,
            'schema_version', 1, 'occurred_at', CURRENT_TIMESTAMP);
        proposed := to_jsonb(NEW);
    END IF;
    FOREACH field_name IN ARRAY ARRAY['created_at','updated_at','accepted_at','verified_at','started_at','ended_at','last_heartbeat_at'] LOOP
        IF proposed ? field_name THEN
            IF field_name = 'created_at' THEN
                proposed := jsonb_set(proposed, ARRAY[field_name],
                    CASE WHEN TG_OP = 'INSERT' THEN to_jsonb(CURRENT_TIMESTAMP) ELSE previous->field_name END);
            ELSIF field_name = 'updated_at' THEN proposed := jsonb_set(proposed, ARRAY[field_name], to_jsonb(CURRENT_TIMESTAMP));
            ELSIF proposed->field_name <> 'null'::jsonb AND
                  (TG_OP = 'INSERT' OR proposed->field_name IS DISTINCT FROM previous->field_name) THEN
                proposed := jsonb_set(proposed, ARRAY[field_name], to_jsonb(CURRENT_TIMESTAMP));
            END IF;
        END IF;
    END LOOP;
    NEW := jsonb_populate_record(NEW, proposed);
    RETURN NEW;
END $$;
"""


AUDIT_CHANGE = r"""
CREATE OR REPLACE FUNCTION public.hermes_audit_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE changed jsonb; before_state text; after_state text; aggregate_version integer;
BEGIN
    SELECT COALESCE(jsonb_agg(key ORDER BY key), '[]'::jsonb) INTO changed FROM jsonb_each(to_jsonb(NEW))
    WHERE key NOT IN ('last_heartbeat_at','lease_expires_at','updated_at')
        AND (TG_OP = 'INSERT' OR value IS DISTINCT FROM to_jsonb(OLD)->key);
    IF TG_OP = 'UPDATE' AND changed = '[]'::jsonb THEN RETURN NEW; END IF;
    before_state := CASE WHEN TG_OP = 'UPDATE' THEN COALESCE(to_jsonb(OLD)->>'state', to_jsonb(OLD)->>'status') END;
    after_state := COALESCE(to_jsonb(NEW)->>'state', to_jsonb(NEW)->>'status');
    IF TG_OP = 'UPDATE' THEN
        UPDATE public.coordination_entities SET version = version + 1 WHERE id = NEW.id RETURNING version INTO aggregate_version;
    ELSE aggregate_version := 1; END IF;
    INSERT INTO public.audit_events(entity_type, entity_id, actor, action, payload, correlation_id)
    VALUES (TG_TABLE_NAME, NEW.id, public.hermes_actor(), lower(TG_OP),
        jsonb_build_object('changed_columns', changed, 'previous_state', before_state, 'new_state', after_state), NEW.id);
    IF TG_TABLE_NAME = 'tasks' THEN
        INSERT INTO public.task_events(task_id, actor, action, previous_state, new_state, reason, correlation_id)
        VALUES (NEW.id, public.hermes_actor(), lower(TG_OP), before_state::public."TaskState", NEW.state,
            COALESCE(NULLIF(current_setting('hermes.audit_reason', true), ''), 'database mutation'), NEW.id);
    END IF;
    INSERT INTO public.outbox_events(entity_id, entity_type, entity_version, event_type, envelope, topic)
    VALUES (NEW.id, TG_TABLE_NAME, aggregate_version, TG_TABLE_NAME || '.changed',
        jsonb_build_object('schema_version', 1, 'entity_id', NEW.id, 'entity_type', TG_TABLE_NAME,
            'entity_version', aggregate_version, 'occurred_at', CURRENT_TIMESTAMP, 'correlation_id', NEW.id),
        'hermes/coordination/v1/' || COALESCE(NULLIF(current_setting('hermes.environment', true), ''), 'default') || '/' ||
        CASE TG_TABLE_NAME WHEN 'tasks' THEN 'tasks/' || NEW.id::text
            WHEN 'workflows' THEN 'workflows/' || NEW.id::text
            WHEN 'work_proposals' THEN 'proposals/' || NEW.id::text
            WHEN 'approval_requests' THEN 'approvals/' || NEW.id::text
            WHEN 'artifact_metadata' THEN 'artifacts/' || NEW.id::text
            WHEN 'assignments' THEN 'nodes/' || (SELECT node_id::text FROM public.agent_instances WHERE id = (to_jsonb(NEW)->>'instance_id')::uuid) || '/assignments'
            ELSE 'registry' END || '/changed');
    RETURN NEW;
END $$;
"""


WRITE_OWNED_RECORD = r"""
CREATE OR REPLACE FUNCTION public.hermes_write_owned_record(p_table text, p_attempt uuid, p_token uuid, p_data jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE attempt public.task_attempts%ROWTYPE; assigned public.assignments%ROWTYPE; agent_id uuid; record_id uuid; artifact_id uuid;
BEGIN
    IF jsonb_typeof(p_data) IS DISTINCT FROM 'object' OR octet_length(p_data::text) > 65536 THEN
        RAISE EXCEPTION 'Owned-write contract must be a bounded JSON object';
    END IF;
    IF p_data ? 'artifact_ids' AND (jsonb_typeof(p_data->'artifact_ids') <> 'array' OR jsonb_array_length(p_data->'artifact_ids') > 100) THEN
        RAISE EXCEPTION 'Artifact references must be a bounded array';
    END IF;
    attempt := public.hermes_owned_attempt(p_attempt, p_token);
    IF attempt.state <> 'running' THEN RAISE EXCEPTION 'Running ownership required'; END IF;
    SELECT * INTO assigned FROM public.assignments WHERE id = attempt.assignment_id;
    SELECT instance.agent_id INTO agent_id FROM public.agent_instances instance WHERE id = assigned.instance_id;
    IF p_table = 'execution_runs' THEN
        INSERT INTO public.execution_runs(attempt_id, node_execution_id, profile_identity, adapter_version)
        VALUES (p_attempt, p_data->>'node_execution_id', agent_id, p_data->>'adapter_version') RETURNING id INTO record_id;
    ELSIF p_table = 'work_proposals' THEN
        IF (p_data->>'ttl_seconds')::integer NOT BETWEEN 1 AND 604800 THEN RAISE EXCEPTION 'Invalid proposal TTL'; END IF;
        INSERT INTO public.work_proposals(proposing_agent_id, proposing_instance_id, originating_task_id, originating_attempt_id,
            proposal_type, state, payload_json, payload_digest, expiry_at, submission_idempotency_key, predecessor_id)
        VALUES (agent_id, assigned.instance_id, attempt.task_id, p_attempt, p_data->>'proposal_type',
            'draft', p_data->'payload_json', repeat('0',64),
            CURRENT_TIMESTAMP + make_interval(secs => (p_data->>'ttl_seconds')::integer), p_data->>'submission_idempotency_key',
            (p_data->>'predecessor_id')::uuid) RETURNING id INTO record_id;
        FOR artifact_id IN SELECT value::uuid FROM jsonb_array_elements_text(COALESCE(p_data->'artifact_ids','[]'::jsonb)) LOOP
            IF NOT EXISTS (SELECT 1 FROM public.artifact_metadata WHERE id = artifact_id AND state = 'accepted'
                AND (producing_attempt_id = p_attempt OR id IN (SELECT input.artifact_id FROM public.task_input_references input WHERE input.task_id = attempt.task_id))) THEN
                RAISE EXCEPTION 'Proposal artifact is not an authorized accepted input/output';
            END IF;
            INSERT INTO public.proposal_artifacts(proposal_id, artifact_id) VALUES (record_id, artifact_id);
        END LOOP;
        UPDATE public.work_proposals SET state = 'submitted' WHERE id = record_id;
    ELSIF p_table = 'artifact_metadata' THEN
        INSERT INTO public.artifact_metadata(producing_task_id, producing_attempt_id, uploader_identity, artifact_kind,
            media_type, byte_size, checksum_digest, export_identifier, environment, storage_key, original_filename,
            provenance, retention_policy, state, verified_at)
        VALUES (attempt.task_id, p_attempt, public.hermes_actor(), p_data->>'artifact_kind', p_data->>'media_type',
            (p_data->>'byte_size')::bigint, p_data->>'checksum_digest', p_data->>'export_identifier', p_data->>'environment',
            p_data->>'storage_key', p_data->>'original_filename', p_data->'provenance', p_data->'retention_policy', 'verified', CURRENT_TIMESTAMP)
        RETURNING id INTO record_id;
    ELSIF p_table = 'approval_requests' THEN
        IF (p_data->>'ttl_seconds')::integer NOT BETWEEN 1 AND 604800 THEN RAISE EXCEPTION 'Invalid approval TTL'; END IF;
        IF EXISTS (SELECT 1 FROM public.execution_runs WHERE attempt_id = p_attempt AND terminal_status IS NULL) THEN
            RAISE EXCEPTION 'Contain/stop execution and persist its terminal outcome before suspending ownership';
        END IF;
        INSERT INTO public.approval_requests(task_id, attempt_id, policy_id, policy_revision, action_digest,
            action_specification, requesting_principal, expires_at)
        VALUES (attempt.task_id, p_attempt, (p_data->>'policy_id')::uuid, (p_data->>'policy_revision')::integer,
            repeat('0',64), p_data->'action_specification', public.hermes_actor(),
            CURRENT_TIMESTAMP + make_interval(secs => (p_data->>'ttl_seconds')::integer)) RETURNING id INTO record_id;
        FOR artifact_id IN SELECT value::uuid FROM jsonb_array_elements_text(COALESCE(p_data->'artifact_ids','[]'::jsonb)) LOOP
            INSERT INTO public.approval_request_artifacts(request_id, artifact_id, checksum_digest)
            SELECT record_id, id, checksum_digest FROM public.artifact_metadata WHERE id = artifact_id AND state = 'accepted'
                AND (producing_attempt_id = p_attempt OR id IN (SELECT input.artifact_id FROM public.task_input_references input WHERE input.task_id = attempt.task_id));
            IF NOT FOUND THEN RAISE EXCEPTION 'Approval artifact is not an authorized accepted input/output'; END IF;
        END LOOP;
        UPDATE public.task_attempts SET state = 'waiting_approval', ended_at = CURRENT_TIMESTAMP WHERE id = p_attempt;
        UPDATE public.tasks SET state = 'waiting_approval' WHERE id = attempt.task_id;
        UPDATE public.assignments SET state = 'completed' WHERE id = assigned.id;
    ELSIF p_table = 'effect_intents' THEN
        INSERT INTO public.effect_intents(task_id, attempt_id, action_digest, approval_request_id, business_idempotency_key)
        SELECT attempt.task_id, p_attempt, request.action_digest, request.id, p_data->>'business_idempotency_key'
        FROM public.approval_requests request WHERE request.id = (p_data->>'approval_request_id')::uuid
            AND request.task_id = attempt.task_id RETURNING id INTO record_id;
        IF record_id IS NULL THEN RAISE EXCEPTION 'Approval belongs to a different task'; END IF;
    ELSE RAISE EXCEPTION 'Unsupported owned write'; END IF;
    RETURN record_id;
END $$;
"""


REGISTER_INSTANCE = r"""
CREATE OR REPLACE FUNCTION public.hermes_register_instance(p_agent uuid, p_version text, p_digest text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE target_node uuid; instance_id uuid;
BEGIN
    SELECT node_id INTO target_node FROM public.principals WHERE id = public.hermes_actor() AND kind = 'node';
    IF target_node IS NULL OR NOT EXISTS (SELECT 1 FROM public.nodes WHERE id = target_node AND desired_state = 'enabled') THEN
        RAISE EXCEPTION 'Enabled registered node required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.agents WHERE id = p_agent AND desired_state = 'enabled' AND approved_profile_revision = p_digest) THEN
        RAISE EXCEPTION 'Profile is not approved';
    END IF;
    PERFORM 1 FROM public.agent_placements WHERE agent_id = p_agent AND node_id = target_node AND enabled FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Agent is not placed on this node' USING ERRCODE = '42501'; END IF;
    INSERT INTO public.agent_instances(agent_id, node_id, hermes_version, verified_profile_digest)
    VALUES (p_agent, target_node, p_version, p_digest) RETURNING id INTO instance_id;
    RETURN instance_id;
END $$;
"""


EVENT_MAINTENANCE = r"""
CREATE FUNCTION public.hermes_ensure_event_partitions(months_ahead integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public SET TimeZone = 'UTC'
SET statement_timeout = '10min' SET transaction_timeout = '15min' AS $$
DECLARE table_name text; partition_name text; month_start timestamptz; month_end timestamptz; created integer := 0;
BEGIN
    IF NOT pg_has_role(session_user, 'hermes_admin', 'MEMBER') THEN
        RAISE EXCEPTION 'Partition maintenance requires admin membership' USING ERRCODE = '42501';
    END IF;
    IF months_ahead IS NULL OR months_ahead NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'Partition horizon must be between 1 and 120 months' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(734821, 2);
    PERFORM set_config('hermes.archiving', 'on', true);
    FOREACH table_name IN ARRAY ARRAY['audit_events','task_events','outbox_events'] LOOP
        EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', table_name);
        FOR month_start IN SELECT generate_series(date_trunc('month', CURRENT_TIMESTAMP),
            date_trunc('month', CURRENT_TIMESTAMP) + make_interval(months => months_ahead), interval '1 month') LOOP
            month_end := month_start + interval '1 month';
            partition_name := table_name || '_' || to_char(month_start AT TIME ZONE 'UTC', 'YYYYMM');
            IF to_regclass(format('public.%I', partition_name)) IS NULL THEN
                EXECUTE format('CREATE TABLE public.%I (LIKE public.%I INCLUDING DEFAULTS INCLUDING CONSTRAINTS)', partition_name, table_name);
                EXECUTE format('WITH moved AS (DELETE FROM public.%I WHERE created_at >= $1 AND created_at < $2 RETURNING *)
                    INSERT INTO public.%I SELECT * FROM moved', table_name || '_default', partition_name) USING month_start, month_end;
                EXECUTE format('ALTER TABLE public.%I ATTACH PARTITION public.%I FOR VALUES FROM (%L) TO (%L)',
                    table_name, partition_name, month_start, month_end);
                created := created + 1;
            ELSIF NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = to_regclass(format('public.%I', partition_name))
                AND inhparent = to_regclass(format('public.%I', table_name))) THEN
                RAISE EXCEPTION 'Expected partition name is occupied: %', partition_name USING ERRCODE = '55000';
            END IF;
        END LOOP;
    END LOOP;
    PERFORM set_config('hermes.archiving', 'off', true);
    RETURN created;
END $$;
REVOKE ALL ON FUNCTION public.hermes_ensure_event_partitions(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.hermes_ensure_event_partitions(integer) TO hermes_admin;

CREATE OR REPLACE FUNCTION public.hermes_archive_events(p_before timestamptz, p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public SET TimeZone = 'UTC'
SET statement_timeout = '10min' SET transaction_timeout = '15min' AS $$
DECLARE counts jsonb := '{}'::jsonb; deleted bigint; actor uuid; table_name text; retention_days integer;
BEGIN
    IF NOT pg_has_role(session_user, 'hermes_admin', 'MEMBER') THEN
        RAISE EXCEPTION 'Archival requires admin membership' USING ERRCODE = '42501';
    END IF;
    retention_days := COALESCE(NULLIF(current_setting('hermes.event_retention_days', true), ''), '90')::integer;
    IF retention_days NOT BETWEEN 1 AND 36500 OR p_before IS NULL
       OR p_before > CURRENT_TIMESTAMP - make_interval(days => retention_days)
       OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 4096 THEN
        RAISE EXCEPTION 'Positive retention floor, eligible cutoff and reason required' USING ERRCODE = '22023';
    END IF;
    actor := public.hermes_actor();
    PERFORM pg_advisory_xact_lock(734821, 2);
    PERFORM set_config('hermes.archiving', 'on', true);
    FOREACH table_name IN ARRAY ARRAY['outbox_events','task_events','audit_events'] LOOP
        EXECUTE format('WITH candidates AS (
            SELECT tableoid AS partition_id, ctid AS row_id FROM public.%I WHERE created_at < $1 %s
                ORDER BY created_at,id LIMIT 5000 FOR UPDATE SKIP LOCKED
            ) DELETE FROM public.%I event USING candidates
              WHERE event.tableoid = candidates.partition_id AND event.ctid = candidates.row_id',
            table_name, CASE WHEN table_name = 'outbox_events' THEN 'AND publication_status = ''published''' ELSE '' END,
            table_name) USING p_before;
        GET DIAGNOSTICS deleted = ROW_COUNT;
        counts := counts || jsonb_build_object(table_name, deleted);
    END LOOP;
    PERFORM set_config('hermes.archiving', 'off', true);
    INSERT INTO public.audit_events(entity_type, entity_id, actor, action, payload, correlation_id)
    VALUES ('principals', actor, actor, 'events.archived', jsonb_build_object('before', p_before, 'reason', p_reason,
        'retention_days', retention_days, 'batch_limit', 5000, 'deleted', counts), actor);
    RETURN counts;
END $$;
"""