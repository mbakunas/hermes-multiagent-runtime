"""Architecture v1.0: complete Phase 1 coordination schema.

Revision ID: 0001_phase1
Revises: None
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


revision: str = "0001_phase1"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS = {
    "NodeState": ("enrolled", "enabled", "draining", "disabled", "retired"),
    "AgentState": ("provisioned", "enabled", "disabled", "retired"),
    "AgentRole": ("concierge", "orchestrator", "researcher", "librarian", "correspondent", "coder"),
    "InstanceState": ("registering", "ready", "draining", "stopped", "suspect", "offline"),
    "ReadinessState": ("not_ready", "ready", "degraded"),
    "RequestStatus": ("submitted", "accepted", "rejected", "completed", "cancelled"),
    "WorkflowStatus": ("pending", "running", "waiting_approval", "succeeded", "failed", "cancelled"),
    "TaskState": ("pending", "blocked", "ready", "assigned", "running", "waiting_approval", "retry_wait", "succeeded", "failed", "cancelled"),
    "AssignmentState": ("active", "completed", "revoked"),
    "AttemptState": ("assigned", "running", "succeeded", "failed", "cancelled", "expired", "waiting_approval"),
    "RequirementKind": ("required", "optional", "preferred"),
    "ProposalState": ("draft", "submitted", "accepted", "rejected", "expired"),
    "ApprovalState": ("pending", "approved", "rejected", "expired", "cancelled", "revoked"),
    "ApprovalDecision": ("approve", "reject"),
    "ArtifactState": ("staged", "verified", "accepted", "retired", "deletion_pending", "deleted", "quarantined"),
    "OutboxPublicationStatus": ("pending", "publishing", "published"),
    "PrincipalKind": ("user", "service", "node"),
    "PolicyActivationState": ("active", "retired"),
    "EffectStatus": ("prepared", "dispatched", "succeeded", "failed", "unknown", "cancelled"),
    "DependencyCondition": ("success", "failure", "terminal"),
    "ProposalDecision": ("accept", "reject", "expire"),
    "ExecutionStatus": ("succeeded", "failed", "interrupted", "deadline_exceeded", "unknown"),
    "AcceptedOutcome": ("succeeded", "failed", "cancelled"),
}


def column(name, datatype, nullable=False, default=None):
    return sa.Column(name, datatype, nullable=nullable,
                     server_default=None if default is None else sa.text(default))


def uid(name, nullable=False, default=None):
    return column(name, pg.UUID(as_uuid=True), nullable, default)


def string(name, length=128, nullable=False):
    return column(name, sa.String(length), nullable)


def timestamp(name, nullable=False, current=False):
    return column(name, sa.DateTime(timezone=True), nullable,
                  "CURRENT_TIMESTAMP" if current else None)


def enum(name, enum_name, default=None, nullable=False):
    return column(name, pg.ENUM(*ENUMS[enum_name], name=enum_name, create_type=False), nullable,
                  None if default is None else f"'{default}'")


def json(name, default=None):
    return column(name, pg.JSONB(), default=None if default is None else f"'{default}'::jsonb")


def created():
    return [timestamp("created_at", current=True)]


def timestamps():
    return [*created(), timestamp("updated_at", current=True)]


TABLE_COLUMNS = {
    "coordination_entities": [string("entity_type", 63), column("version", sa.Integer(), default="1"), *created()],
    "principals": [string("subject", 255), enum("kind", "PrincipalKind"), string("database_role", 63, True), uid("node_id", True), *created()],
    "nodes": [string("name"), string("enrollment_identity", 255), enum("desired_state", "NodeState", "enrolled"), timestamp("last_heartbeat_at", True), json("metadata_json", "{}"), *timestamps()],
    "agents": [string("name"), enum("role", "AgentRole"), enum("desired_state", "AgentState", "provisioned"), string("approved_profile_revision"), *timestamps()],
    "agent_instances": [uid("agent_id"), uid("node_id"), string("hermes_version"), string("verified_profile_digest"), enum("state", "InstanceState", "registering"), enum("readiness", "ReadinessState", "not_ready"), column("capacity", sa.Integer(), default="1"), timestamp("last_heartbeat_at", True), *timestamps()],
    "capability_definitions": [string("canonical_name"), column("contract_version", sa.Integer(), default="1"), string("description", 4096), json("approval_requirements", "{}"), *created()],
    "instance_capabilities": [uid("instance_id"), uid("capability_id"), string("verified_source_revision"), timestamp("verified_at", current=True)],
    "requests": [uid("authenticated_initiator"), uid("concierge_reference", True), uid("intent_reference"), string("idempotency_key", 255), enum("status", "RequestStatus", "submitted"), *timestamps()],
    "workflows": [uid("request_id"), string("policy_revision"), enum("status", "WorkflowStatus", "pending"), json("budget_json", "{}"), timestamp("deadline_at", True), column("version", sa.Integer(), default="1"), *timestamps()],
    "tasks": [uid("workflow_id"), string("specification_revision"), json("output_contract"), column("priority", sa.Integer(), default="0"), timestamp("available_at", current=True), timestamp("deadline_at", True), enum("state", "TaskState", "pending"), column("cancellation_intent", sa.Boolean(), default="false"), uid("current_attempt_id", True), column("version", sa.Integer(), default="1"), *timestamps()],
    "task_input_references": [uid("task_id"), uid("artifact_id", True), string("external_reference", 2048, True)],
    "task_capabilities": [uid("task_id"), uid("capability_id"), string("capability_name"), string("version_constraint"), enum("requirement_kind", "RequirementKind")],
    "task_dependencies": [uid("prerequisite_task_id"), uid("dependent_task_id"), enum("condition", "DependencyCondition", "success")],
    "assignments": [uid("task_id"), uid("instance_id"), uid("authoritative_assigner"), string("selection_rationale", 4096), json("capability_snapshot"), enum("state", "AssignmentState", "active"), *timestamps()],
    "task_attempts": [uid("task_id"), uid("assignment_id"), column("attempt_number", sa.Integer()), enum("state", "AttemptState", "assigned"), uid("ownership_token", default="gen_random_uuid()"), timestamp("lease_expires_at"), timestamp("started_at", True), timestamp("ended_at", True), string("error_classification", 128, True), *timestamps()],
    "execution_runs": [uid("attempt_id"), string("node_execution_id", 255), string("hermes_session_id", 255, True), uid("profile_identity"), string("adapter_version"), enum("terminal_status", "ExecutionStatus", nullable=True), json("usage_metadata", "{}"), timestamp("started_at", current=True), timestamp("ended_at", True)],
    "task_results": [uid("attempt_id"), enum("accepted_outcome", "AcceptedOutcome"), string("summary", 4096), json("coordination_data", "{}"), timestamp("accepted_at", current=True)],
    "task_result_artifacts": [uid("result_id"), uid("artifact_id")],
    "work_proposals": [uid("proposing_agent_id"), uid("proposing_instance_id"), uid("originating_task_id"), uid("originating_attempt_id"), string("proposal_type"), column("revision", sa.Integer(), default="1"), enum("state", "ProposalState", "draft"), json("payload_json"), string("payload_digest", 64), timestamp("expiry_at"), string("submission_idempotency_key", 255), uid("predecessor_id", True), *timestamps()],
    "proposal_artifacts": [uid("proposal_id"), uid("artifact_id")],
    "proposal_tasks": [uid("proposal_id"), uid("task_id")],
    "proposal_decisions": [uid("proposal_id"), column("proposal_revision", sa.Integer()), uid("orchestrator_principal"), enum("decision", "ProposalDecision"), string("reason", 4096), string("policy_revision"), *created()],
    "approval_policies": [column("revision", sa.Integer()), string("action_category"), json("scope_rules"), string("required_approver_role"), json("expiry_rules"), string("policy_digest", 64), enum("activation_state", "PolicyActivationState"), *created()],
    "approval_requests": [uid("task_id"), uid("attempt_id"), uid("policy_id"), column("policy_revision", sa.Integer()), column("revision", sa.Integer(), default="1"), string("action_digest", 64), json("action_specification"), uid("requesting_principal"), enum("state", "ApprovalState", "pending"), timestamp("expires_at"), *timestamps()],
    "approval_request_artifacts": [uid("request_id"), uid("artifact_id"), string("checksum_digest", 64)],
    "approval_decisions": [uid("request_id"), column("request_revision", sa.Integer()), uid("authenticated_user"), enum("decision", "ApprovalDecision"), string("reason", 4096), string("idempotency_key", 255), *created()],
    "effect_intents": [uid("task_id"), uid("attempt_id"), string("action_digest", 64), uid("approval_request_id"), string("business_idempotency_key", 255), enum("execution_status", "EffectStatus", "prepared"), string("external_reference", 2048, True), *timestamps()],
    "artifact_metadata": [uid("producing_task_id", True), uid("producing_attempt_id", True), uid("uploader_identity"), string("artifact_kind"), string("media_type", 255), column("byte_size", sa.BigInteger()), string("checksum_algorithm", 16), string("checksum_digest", 64), string("provider", 32), string("export_identifier"), string("environment", 64), string("storage_key", 512), string("original_filename", 255), json("provenance"), enum("state", "ArtifactState", "staged"), json("retention_policy"), timestamp("retention_expires_at", True), column("retention_hold", sa.Boolean(), default="false"), timestamp("verified_at", True), timestamp("accepted_at", True), uid("supersedes_id", True), *timestamps()],
    "artifact_lineage": [uid("artifact_id"), uid("source_artifact_id")],
    "task_events": [uid("task_id"), uid("actor"), string("action"), enum("previous_state", "TaskState", nullable=True), enum("new_state", "TaskState"), string("reason", 4096), uid("correlation_id"), uid("causation_id", True), *created()],
    "audit_events": [string("entity_type", 63), uid("entity_id"), uid("actor"), string("action"), json("payload"), uid("correlation_id"), uid("causation_id", True), *created()],
    "outbox_events": [uid("entity_id"), string("entity_type", 63), column("entity_version", sa.Integer()), string("event_type"), json("envelope"), string("topic", 512), enum("publication_status", "OutboxPublicationStatus", "pending"), uid("publisher_lease", True), timestamp("publisher_lease_expires_at", True), timestamp("retry_at", current=True), *created()],
}

TABLE_COLUMNS["artifact_metadata"][6].server_default = sa.DefaultClause(sa.text("'sha256'"))
TABLE_COLUMNS["artifact_metadata"][8].server_default = sa.DefaultClause(sa.text("'nas'"))

UNIQUE = {
    "coordination_entities": [("id", "entity_type")],
    "principals": [("subject",), ("database_role",)],
    "nodes": [("name",), ("enrollment_identity",)],
    "agents": [("name",)],
    "agent_instances": [("id", "agent_id")],
    "capability_definitions": [("canonical_name", "contract_version"), ("id", "canonical_name")],
    "instance_capabilities": [("instance_id", "capability_id")],
    "requests": [("idempotency_key",)],
    "tasks": [("id", "workflow_id")],
    "task_input_references": [("task_id", "artifact_id"), ("task_id", "external_reference")],
    "task_capabilities": [("task_id", "capability_name")],
    "task_dependencies": [("prerequisite_task_id", "dependent_task_id")],
    "assignments": [("id", "task_id")],
    "task_attempts": [("task_id", "attempt_number"), ("id", "task_id"), ("assignment_id",), ("ownership_token",)],
    "execution_runs": [("attempt_id",), ("node_execution_id",)],
    "task_results": [("attempt_id",)],
    "task_result_artifacts": [("result_id", "artifact_id")],
    "work_proposals": [("id", "revision"), ("submission_idempotency_key",)],
    "proposal_artifacts": [("proposal_id", "artifact_id")],
    "proposal_tasks": [("proposal_id", "task_id")],
    "proposal_decisions": [("proposal_id", "proposal_revision")],
    "approval_policies": [("action_category", "revision"), ("id", "revision")],
    "approval_requests": [("id", "revision"), ("id", "task_id", "attempt_id", "action_digest")],
    "approval_request_artifacts": [("request_id", "artifact_id")],
    "approval_decisions": [("request_id", "request_revision"), ("idempotency_key",)],
    "effect_intents": [("business_idempotency_key",)],
    "artifact_metadata": [("id", "checksum_digest"), ("provider", "export_identifier", "environment", "storage_key")],
    "artifact_lineage": [("artifact_id", "source_artifact_id")],
}

FOREIGN_KEYS = {
    "principals": [("node_id", "nodes", "id")],
    "agent_instances": [("agent_id", "agents", "id"), ("node_id", "nodes", "id")],
    "instance_capabilities": [("instance_id", "agent_instances", "id"), ("capability_id", "capability_definitions", "id")],
    "requests": [("authenticated_initiator", "principals", "id"), ("concierge_reference", "agents", "id"), ("intent_reference", "artifact_metadata", "id")],
    "workflows": [("request_id", "requests", "id")],
    "tasks": [("workflow_id", "workflows", "id"), (("current_attempt_id", "id"), "task_attempts", ("id", "task_id"))],
    "task_input_references": [("task_id", "tasks", "id"), ("artifact_id", "artifact_metadata", "id")],
    "task_capabilities": [("task_id", "tasks", "id"), (("capability_id", "capability_name"), "capability_definitions", ("id", "canonical_name"))],
    "task_dependencies": [("prerequisite_task_id", "tasks", "id"), ("dependent_task_id", "tasks", "id")],
    "assignments": [("task_id", "tasks", "id"), ("instance_id", "agent_instances", "id"), ("authoritative_assigner", "principals", "id")],
    "task_attempts": [("task_id", "tasks", "id"), (("assignment_id", "task_id"), "assignments", ("id", "task_id"))],
    "execution_runs": [("attempt_id", "task_attempts", "id"), ("profile_identity", "agents", "id")],
    "task_results": [("attempt_id", "task_attempts", "id")],
    "task_result_artifacts": [("result_id", "task_results", "id"), ("artifact_id", "artifact_metadata", "id")],
    "work_proposals": [("proposing_agent_id", "agents", "id"), ("originating_task_id", "tasks", "id"), (("proposing_instance_id", "proposing_agent_id"), "agent_instances", ("id", "agent_id")), (("originating_attempt_id", "originating_task_id"), "task_attempts", ("id", "task_id")), ("predecessor_id", "work_proposals", "id")],
    "proposal_artifacts": [("proposal_id", "work_proposals", "id"), ("artifact_id", "artifact_metadata", "id")],
    "proposal_tasks": [("proposal_id", "work_proposals", "id"), ("task_id", "tasks", "id")],
    "proposal_decisions": [(("proposal_id", "proposal_revision"), "work_proposals", ("id", "revision")), ("orchestrator_principal", "principals", "id")],
    "approval_requests": [("task_id", "tasks", "id"), (("attempt_id", "task_id"), "task_attempts", ("id", "task_id")), (("policy_id", "policy_revision"), "approval_policies", ("id", "revision")), ("requesting_principal", "principals", "id")],
    "approval_request_artifacts": [("request_id", "approval_requests", "id"), (("artifact_id", "checksum_digest"), "artifact_metadata", ("id", "checksum_digest"))],
    "approval_decisions": [(("request_id", "request_revision"), "approval_requests", ("id", "revision")), ("authenticated_user", "principals", "id")],
    "effect_intents": [("task_id", "tasks", "id"), (("attempt_id", "task_id"), "task_attempts", ("id", "task_id")), (("approval_request_id", "task_id", "attempt_id", "action_digest"), "approval_requests", ("id", "task_id", "attempt_id", "action_digest"))],
    "artifact_metadata": [("producing_task_id", "tasks", "id"), (("producing_attempt_id", "producing_task_id"), "task_attempts", ("id", "task_id")), ("uploader_identity", "principals", "id"), ("supersedes_id", "artifact_metadata", "id")],
    "artifact_lineage": [("artifact_id", "artifact_metadata", "id"), ("source_artifact_id", "artifact_metadata", "id")],
    "task_events": [("task_id", "tasks", "id"), ("actor", "principals", "id"), ("correlation_id", "coordination_entities", "id"), ("causation_id", "coordination_entities", "id")],
    "audit_events": [(("entity_id", "entity_type"), "coordination_entities", ("id", "entity_type")), ("actor", "principals", "id"), ("correlation_id", "coordination_entities", "id"), ("causation_id", "coordination_entities", "id")],
    "outbox_events": [(("entity_id", "entity_type"), "coordination_entities", ("id", "entity_type"))],
}

CHECKS = {
    "coordination_entities": [("version", "version > 0")],
    "principals": [("node_binding", "(kind = 'node') = (node_id IS NOT NULL)")],
    "agent_instances": [("phase_one_capacity", "capacity = 1")],
    "capability_definitions": [("canonical_name", "canonical_name ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'"), ("positive_version", "contract_version > 0")],
    "workflows": [("version", "version > 0")],
    "tasks": [("version", "version > 0"), ("deadline", "deadline_at IS NULL OR deadline_at >= available_at")],
    "task_input_references": [("one_source", "(artifact_id IS NOT NULL)::integer + (external_reference IS NOT NULL)::integer = 1")],
    "task_dependencies": [("not_self", "prerequisite_task_id <> dependent_task_id")],
    "task_attempts": [("positive_attempt", "attempt_number > 0"), ("time_order", "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at")],
    "work_proposals": [("revision", "revision > 0"), ("payload_digest", "payload_digest ~ '^[0-9a-f]{64}$'")],
    "approval_policies": [("revision", "revision > 0"), ("policy_digest", "policy_digest ~ '^[0-9a-f]{64}$'")],
    "approval_requests": [("revision", "revision > 0"), ("action_digest", "action_digest ~ '^[0-9a-f]{64}$'")],
    "artifact_metadata": [("byte_size", "byte_size >= 0"), ("checksum", "checksum_algorithm = 'sha256' AND checksum_digest ~ '^[0-9a-f]{64}$'"), ("producing_origin", "(producing_task_id IS NOT NULL AND producing_attempt_id IS NOT NULL) OR (producing_task_id IS NULL AND producing_attempt_id IS NULL AND COALESCE(provenance->>'origin', '') = 'external_intake')"), ("phase_one_provider", "provider = 'nas'"), ("relative_key", "storage_key !~ '(^/|(^|/)\\.\\.(/|$))'")],
    "artifact_lineage": [("not_self", "artifact_id <> source_artifact_id")],
    "outbox_events": [("entity_version", "entity_version > 0"), ("publisher_ownership", "(publication_status = 'publishing' AND publisher_lease IS NOT NULL AND publisher_lease_expires_at IS NOT NULL) OR (publication_status <> 'publishing' AND publisher_lease IS NULL AND publisher_lease_expires_at IS NULL)")],
}

INDEXES = {
    "agent_instances": [("uq_agent_instances_active_incarnation", ("agent_id",), True, "state IN ('registering', 'ready', 'draining', 'suspect')"), ("ix_agent_instances_eligibility", ("state", "readiness", "last_heartbeat_at"), False, None)],
    "tasks": [("ix_tasks_schedule", ("state", "available_at", "deadline_at"), False, None)],
    "assignments": [("uq_assignments_active_task", ("task_id",), True, "state = 'active'"), ("uq_assignments_active_instance", ("instance_id",), True, "state = 'active'")],
    "task_attempts": [("uq_task_attempts_active_task", ("task_id",), True, "state IN ('assigned', 'running')"), ("ix_task_attempts_leases", ("lease_expires_at",), False, "state IN ('assigned', 'running')")],
    "work_proposals": [("ix_work_proposals_review", ("state", "expiry_at"), False, None)],
    "approval_requests": [("ix_approval_requests_pending", ("expires_at",), False, "state = 'pending'")],
    "artifact_metadata": [("ix_artifact_metadata_retention", ("state", "retention_expires_at"), False, None)],
    "audit_events": [("ix_audit_events_entity", ("entity_id", "created_at"), False, None)],
    "outbox_events": [("ix_outbox_events_pending", ("retry_at", "created_at"), False, "publication_status <> 'published'")],
}

SIMPLE_INDEXES = {
    "principals": ("node_id",), "agent_instances": ("agent_id", "node_id"),
    "instance_capabilities": ("instance_id", "capability_id"),
    "requests": ("authenticated_initiator", "concierge_reference", "intent_reference"),
    "workflows": ("request_id",), "tasks": ("workflow_id", "deadline_at", "current_attempt_id"),
    "task_input_references": ("task_id", "artifact_id"), "task_capabilities": ("task_id", "capability_id"),
    "task_dependencies": ("prerequisite_task_id", "dependent_task_id"),
    "assignments": ("task_id", "instance_id", "authoritative_assigner"), "task_attempts": ("task_id",),
    "execution_runs": ("profile_identity",), "task_result_artifacts": ("result_id", "artifact_id"),
    "work_proposals": ("proposing_instance_id", "originating_attempt_id", "originating_task_id", "proposing_agent_id", "predecessor_id"),
    "proposal_artifacts": ("proposal_id", "artifact_id"),
    "proposal_tasks": ("proposal_id", "task_id"), "proposal_decisions": ("proposal_id", "orchestrator_principal"),
    "approval_requests": ("attempt_id", "task_id", "policy_id", "requesting_principal"),
    "approval_request_artifacts": ("request_id", "artifact_id"), "approval_decisions": ("request_id", "authenticated_user"),
    "effect_intents": ("attempt_id", "task_id", "approval_request_id"),
    "artifact_metadata": ("producing_task_id", "producing_attempt_id", "uploader_identity", "supersedes_id"),
    "artifact_lineage": ("artifact_id", "source_artifact_id"),
    "task_events": ("task_id", "actor", "correlation_id", "causation_id"),
    "audit_events": ("actor", "correlation_id", "causation_id"),
    "outbox_events": ("publication_status", "retry_at", "entity_id", "publisher_lease_expires_at"),
}


def create_schema() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).create(bind, checkfirst=False)
    for table_name, fields in TABLE_COLUMNS.items():
        constraints = [sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table_name}"))]
        constraints.extend(sa.UniqueConstraint(*fields, name=op.f(f"uq_{table_name}_{'_'.join(fields)}"))
                           for fields in UNIQUE.get(table_name, ()))
        for check_name, expression in CHECKS.get(table_name, ()):
            constraints.append(sa.CheckConstraint(expression, name=op.f(f"ck_{table_name}_{check_name}")))
        for field in fields:
            if isinstance(field.type, pg.JSONB):
                shape = "array" if field.name == "capability_snapshot" else "object"
                limit = 8192 if field.name == "envelope" else 65536
                constraints.append(sa.CheckConstraint(
                    f"jsonb_typeof({field.name}) = '{shape}' AND octet_length({field.name}::text) <= {limit}",
                    name=op.f(f"ck_{table_name}_{field.name}_bounded"),
                ))
        op.create_table(table_name, uid("id", default="gen_random_uuid()"), *fields, *constraints)
    for table_name in TABLE_COLUMNS:
        references = list(FOREIGN_KEYS.get(table_name, ()))
        if table_name != "coordination_entities":
            references.append(("id", "coordination_entities", "id"))
        for local, remote_table, remote in references:
            local_columns = (local,) if isinstance(local, str) else local
            remote_columns = (remote,) if isinstance(remote, str) else remote
            name = "fk_tasks_current_attempt" if table_name == "tasks" and local_columns[0] == "current_attempt_id" else f"fk_{table_name}_{local_columns[0]}_{remote_table}"
            op.create_foreign_key(op.f(name), table_name, remote_table, local_columns, remote_columns,
                                  ondelete="RESTRICT", deferrable=True, initially="DEFERRED")
    for table_name, fields in SIMPLE_INDEXES.items():
        for field in fields:
            op.create_index(op.f(f"ix_{table_name}_{field}"), table_name, [field])
    for table_name, indexes in INDEXES.items():
        for name, fields, unique, predicate in indexes:
            kwargs = {} if predicate is None else {"postgresql_where": sa.text(predicate)}
            op.create_index(op.f(name), table_name, list(fields), unique=unique, **kwargs)


def upgrade() -> None:
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    create_schema()
    install_security()


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is prohibited. Restore a verified backup into a separate database.")


def install_security() -> None:
    op.execute(SECURITY_FUNCTIONS)
    for table_name in TABLE_COLUMNS:
        if table_name == "coordination_entities":
            continue
        op.execute(f'CREATE TRIGGER a_identity BEFORE INSERT ON public.{table_name} '
                   'FOR EACH ROW EXECUTE FUNCTION public.hermes_register_entity()')
    op.execute("""
        INSERT INTO public.principals(subject, kind, database_role)
        VALUES ('database:' || session_user, 'service', session_user);
    """)
    for table_name in TABLE_COLUMNS:
        op.execute(f'CREATE TRIGGER b_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table_name} '
                   'FOR EACH ROW EXECUTE FUNCTION public.hermes_guard_record()')
        op.execute(f'CREATE TRIGGER z_no_truncate BEFORE TRUNCATE ON public.{table_name} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION public.hermes_no_history_delete()')
        if table_name not in {"coordination_entities", "audit_events", "task_events", "outbox_events"}:
            op.execute(f'CREATE TRIGGER c_audit AFTER INSERT OR UPDATE ON public.{table_name} '
                       'FOR EACH ROW EXECUTE FUNCTION public.hermes_audit_change()')
    op.execute('CREATE TRIGGER c_publication_audit AFTER UPDATE ON public.outbox_events '
               'FOR EACH ROW EXECUTE FUNCTION public.hermes_audit_publication()')
    op.execute("""
        CREATE CONSTRAINT TRIGGER z_task_consistency AFTER INSERT OR UPDATE ON public.tasks
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.hermes_task_consistency();
        CREATE CONSTRAINT TRIGGER z_assignment_consistency AFTER INSERT OR UPDATE ON public.assignments
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.hermes_task_consistency();
        CREATE CONSTRAINT TRIGGER z_attempt_consistency AFTER INSERT OR UPDATE ON public.task_attempts
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.hermes_task_consistency();
        CREATE CONSTRAINT TRIGGER z_proposal_consistency AFTER INSERT OR UPDATE ON public.work_proposals
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.hermes_proposal_consistency();
    """)
    op.execute(OWNERSHIP_FUNCTIONS)
    op.execute(PERMISSIONS)
    for capability in INITIAL_CAPABILITIES:
        op.execute(sa.text("""
            INSERT INTO public.capability_definitions(canonical_name, contract_version, description)
            VALUES (:name, 1, :description)
        """).bindparams(name=capability, description=capability.replace("_", " ")))


INITIAL_CAPABILITIES = (
    "web_research", "citation_generation", "memory_search", "memory_store", "wiki_update", "wiki_search",
    "document_creation", "document_editing", "code_analysis", "code_generation", "code_review",
    "email_drafting", "correspondence_generation", "playwright_browser", "crawl4ai_extraction",
    "searxng_search", "file_analysis", "task_planning", "workflow_orchestration",
)


SECURITY_FUNCTIONS = r"""
DO $$
DECLARE role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['hermes_admin', 'hermes_orchestrator', 'hermes_node',
        'hermes_intake', 'hermes_approval', 'hermes_outbox', 'hermes_reader'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE', role_name);
        ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name
                     AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole)) THEN
            RAISE EXCEPTION 'Unsafe existing Hermes role: %', role_name;
        END IF;
    END LOOP;
    EXECUTE format('GRANT hermes_admin TO %I', session_user);
END $$;

CREATE FUNCTION public.hermes_actor() RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE actor_id uuid;
BEGIN
    SELECT id INTO actor_id FROM public.principals WHERE database_role = session_user;
    IF actor_id IS NULL THEN RAISE EXCEPTION 'Unregistered database login' USING ERRCODE = '42501'; END IF;
    RETURN actor_id;
END $$;

CREATE FUNCTION public.hermes_register_entity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    INSERT INTO public.coordination_entities(id, entity_type) VALUES (NEW.id, TG_TABLE_NAME);
    RETURN NEW;
END $$;

CREATE FUNCTION public.hermes_no_history_delete() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION 'Coordination history cannot be deleted or truncated' USING ERRCODE = '42501';
END $$;

CREATE FUNCTION public.hermes_guard_record() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    previous jsonb;
    proposed jsonb;
    field_name text;
    is_orchestrator boolean := pg_has_role(session_user, 'hermes_orchestrator', 'MEMBER');
    approval public.approval_requests%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Coordination history cannot be deleted' USING ERRCODE = '42501';
    END IF;
    proposed := to_jsonb(NEW);
    IF TG_OP = 'UPDATE' THEN
        previous := to_jsonb(OLD);
        IF NEW.id <> OLD.id THEN RAISE EXCEPTION 'Primary keys are immutable'; END IF;
        IF TG_TABLE_NAME IN ('principals', 'capability_definitions',
            'instance_capabilities', 'task_input_references', 'task_capabilities', 'task_dependencies',
            'proposal_tasks', 'proposal_artifacts', 'proposal_decisions', 'approval_decisions', 'task_results',
            'task_result_artifacts', 'approval_request_artifacts', 'artifact_lineage', 'audit_events', 'task_events') THEN
            RAISE EXCEPTION 'Record is append-only: %', TG_TABLE_NAME;
        END IF;
        IF TG_TABLE_NAME = 'coordination_entities' AND
           ((proposed - 'version') IS DISTINCT FROM (previous - 'version') OR
            (proposed->>'version')::integer <> (previous->>'version')::integer + 1) THEN
            RAISE EXCEPTION 'Entity identity/type is immutable';
        END IF;
        IF TG_TABLE_NAME IN ('tasks', 'workflows') THEN
            IF NEW.version NOT IN (OLD.version, OLD.version + 1) THEN RAISE EXCEPTION 'Invalid version increment'; END IF;
            proposed := jsonb_set(proposed, '{version}', to_jsonb(OLD.version + 1));
        END IF;
        IF TG_TABLE_NAME = 'tasks' AND NOT is_orchestrator AND
           (proposed - ARRAY['state','current_attempt_id','version','updated_at']) IS DISTINCT FROM
           (previous - ARRAY['state','current_attempt_id','version','updated_at']) THEN
            RAISE EXCEPTION 'Only Orchestrator may change task specification or scheduling';
        END IF;
        IF TG_TABLE_NAME = 'tasks' AND NOT is_orchestrator AND
             NOT ((previous->>'state' = 'assigned' AND proposed->>'state' = 'running') OR
                 (previous->>'state' = 'running' AND proposed->>'state' IN ('succeeded','failed','cancelled','waiting_approval'))) THEN
            RAISE EXCEPTION 'Node cannot schedule work';
        END IF;
        IF TG_TABLE_NAME = 'task_attempts' AND proposed->>'ownership_token' IS DISTINCT FROM previous->>'ownership_token' THEN
            RAISE EXCEPTION 'Attempt ownership token is immutable';
        END IF;
        IF TG_TABLE_NAME = 'task_attempts' THEN
            IF NOT ((OLD.state = 'assigned' AND NEW.state IN ('assigned','running','cancelled','expired')) OR
                    (OLD.state = 'running' AND NEW.state IN ('running','succeeded','failed','cancelled','expired','waiting_approval'))) THEN
                RAISE EXCEPTION 'Terminal attempt history is immutable';
            END IF;
            IF (proposed - ARRAY['state','lease_expires_at','started_at','ended_at','error_classification','updated_at'])
               IS DISTINCT FROM (previous - ARRAY['state','lease_expires_at','started_at','ended_at','error_classification','updated_at']) THEN
                RAISE EXCEPTION 'Attempt assignment/identity is immutable';
            END IF;
        END IF;
        IF TG_TABLE_NAME = 'artifact_metadata' AND previous->>'state' IN ('accepted','retired','deletion_pending','deleted') THEN
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
        IF TG_TABLE_NAME = 'work_proposals' THEN
            IF OLD.state <> 'draft' AND
               (proposed - ARRAY['state','updated_at']) IS DISTINCT FROM (previous - ARRAY['state','updated_at']) THEN
                RAISE EXCEPTION 'Submitted proposal is immutable';
            END IF;
            IF NOT ((OLD.state = 'draft' AND NEW.state IN ('draft','submitted')) OR
                    (OLD.state = 'submitted' AND NEW.state IN ('accepted','rejected','expired'))) THEN
                RAISE EXCEPTION 'Invalid proposal transition';
            END IF;
        END IF;
        IF TG_TABLE_NAME = 'approval_requests' THEN
            IF (proposed - ARRAY['state','updated_at']) IS DISTINCT FROM (previous - ARRAY['state','updated_at']) THEN
                RAISE EXCEPTION 'Approval action binding is immutable; create a new request';
            END IF;
            IF NOT ((OLD.state = 'pending' AND NEW.state IN ('approved','rejected','expired','cancelled')) OR
                    (OLD.state = 'approved' AND NEW.state = 'revoked')) THEN
                RAISE EXCEPTION 'Invalid approval transition';
            END IF;
        END IF;
        IF TG_TABLE_NAME = 'approval_policies' AND
           ((proposed - 'activation_state') IS DISTINCT FROM (previous - 'activation_state') OR
            previous->>'activation_state' <> 'active' OR proposed->>'activation_state' <> 'retired') THEN
            RAISE EXCEPTION 'Policy content is immutable; only retirement is allowed';
        END IF;
        IF TG_TABLE_NAME = 'outbox_events' AND
           ((proposed - ARRAY['publication_status','publisher_lease','publisher_lease_expires_at','retry_at'])
            IS DISTINCT FROM (previous - ARRAY['publication_status','publisher_lease','publisher_lease_expires_at','retry_at'])
            OR previous->>'publication_status' = 'published') THEN
            RAISE EXCEPTION 'Outbox event content is immutable';
        END IF;
    END IF;
    IF TG_TABLE_NAME IN ('proposal_tasks', 'proposal_decisions') AND NOT is_orchestrator THEN
        RAISE EXCEPTION 'Orchestrator is the sole assignment authority' USING ERRCODE = '42501';
    END IF;
    IF TG_TABLE_NAME = 'assignments' THEN
        IF NOT is_orchestrator AND NOT (
            TG_OP = 'UPDATE' AND pg_has_role(session_user, 'hermes_node', 'MEMBER')
            AND previous->>'state' = 'active' AND proposed->>'state' = 'completed'
            AND (proposed - ARRAY['state','updated_at']) IS NOT DISTINCT FROM (previous - ARRAY['state','updated_at'])
        ) THEN RAISE EXCEPTION 'Orchestrator is the sole assignment authority' USING ERRCODE = '42501'; END IF;
        IF TG_OP = 'INSERT' AND NEW.authoritative_assigner <> public.hermes_actor() THEN
            RAISE EXCEPTION 'Assignment principal does not match authenticated service';
        END IF;
        IF TG_OP = 'INSERT' THEN
            PERFORM 1 FROM public.tasks WHERE id = NEW.task_id AND state = 'ready' AND NOT cancellation_intent FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Task is not assignable'; END IF;
            IF NOT EXISTS (SELECT 1 FROM public.agent_instances instance
                JOIN public.agents agent ON agent.id = instance.agent_id JOIN public.nodes node ON node.id = instance.node_id
                WHERE instance.id = NEW.instance_id AND instance.state = 'ready' AND instance.readiness = 'ready'
                AND agent.desired_state = 'enabled' AND node.desired_state = 'enabled'
                AND instance.verified_profile_digest = agent.approved_profile_revision) THEN
                RAISE EXCEPTION 'Instance is not eligible';
            END IF;
            IF EXISTS (SELECT 1 FROM public.task_capabilities requirement WHERE requirement.task_id = NEW.task_id
                AND requirement.requirement_kind = 'required' AND NOT EXISTS (
                    SELECT 1 FROM public.instance_capabilities inventory
                    WHERE inventory.instance_id = NEW.instance_id AND inventory.capability_id = requirement.capability_id
                )) THEN RAISE EXCEPTION 'Required capability not registered'; END IF;
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'proposal_decisions' THEN
        IF NEW.orchestrator_principal <> public.hermes_actor() THEN
            RAISE EXCEPTION 'Proposal decision principal mismatch';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'requests' THEN
        IF NEW.concierge_reference IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.agents WHERE id = NEW.concierge_reference AND role = 'concierge'
        ) THEN RAISE EXCEPTION 'Concierge reference must identify a Concierge'; END IF;
    END IF;
    IF TG_TABLE_NAME = 'task_dependencies' THEN
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
    IF TG_TABLE_NAME = 'work_proposals' THEN
        IF NOT EXISTS (SELECT 1 FROM public.task_attempts attempt JOIN public.assignments assignment ON assignment.id = attempt.assignment_id
            WHERE attempt.id = NEW.originating_attempt_id AND assignment.instance_id = NEW.proposing_instance_id) THEN
            RAISE EXCEPTION 'Proposal origin does not match assigned instance';
        END IF;
        NEW.payload_digest := encode(public.digest(NEW.payload_json::text, 'sha256'), 'hex');
        proposed := to_jsonb(NEW);
    END IF;
    IF TG_TABLE_NAME = 'proposal_artifacts' THEN
        PERFORM 1 FROM public.work_proposals WHERE id = NEW.proposal_id AND state = 'draft' FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Submitted proposal artifact references are frozen'; END IF;
    END IF;
    IF TG_TABLE_NAME = 'approval_request_artifacts' THEN
        PERFORM 1 FROM public.approval_requests WHERE id = NEW.request_id AND state = 'pending' FOR UPDATE;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM public.approval_decisions WHERE request_id = NEW.request_id) THEN
            RAISE EXCEPTION 'Decided approval artifact bindings are frozen';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'approval_requests' THEN
        IF TG_OP = 'INSERT' OR NEW.state = 'approved' THEN
            PERFORM 1 FROM public.approval_policies WHERE id = NEW.policy_id AND revision = NEW.policy_revision
                AND activation_state = 'active' FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Approval requires an active policy revision'; END IF;
        END IF;
        NEW.action_digest := encode(public.digest(NEW.action_specification::text, 'sha256'), 'hex');
        proposed := to_jsonb(NEW);
        IF NEW.state IN ('approved','rejected') AND NOT EXISTS (
            SELECT 1 FROM public.approval_decisions
            WHERE request_id = NEW.id AND request_revision = NEW.revision
            AND decision::text = CASE NEW.state WHEN 'approved' THEN 'approve' ELSE 'reject' END
        ) THEN RAISE EXCEPTION 'Approval state requires a durable matching user decision'; END IF;
        IF NEW.state = 'approved' AND NEW.expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'Approval expired';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'approval_decisions' THEN
        SELECT * INTO approval FROM public.approval_requests WHERE id = NEW.request_id FOR UPDATE;
        IF approval.state <> 'pending' OR approval.expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'Approval is not pending and valid';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.principals WHERE id = NEW.authenticated_user AND kind = 'user') THEN
            RAISE EXCEPTION 'An approval requires a registered human principal';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'effect_intents' THEN
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
           (previous - ARRAY['execution_status','external_reference','updated_at']) THEN
            RAISE EXCEPTION 'Effect action binding is immutable';
        END IF;
        IF TG_OP = 'UPDATE' AND NOT (
            (OLD.execution_status = 'prepared' AND NEW.execution_status IN ('dispatched','cancelled')) OR
            (OLD.execution_status = 'dispatched' AND NEW.execution_status IN ('succeeded','failed','unknown')) OR
            (OLD.execution_status = 'unknown' AND NEW.execution_status IN ('succeeded','failed','cancelled'))
        ) THEN RAISE EXCEPTION 'Invalid effect transition; uncertain effects cannot be redispatched'; END IF;
    END IF;
    IF TG_TABLE_NAME = 'outbox_events' AND TG_OP = 'INSERT' THEN
        NEW.envelope := NEW.envelope || jsonb_build_object('event_id', NEW.id, 'event_type', NEW.event_type,
            'schema_version', 1, 'occurred_at', CURRENT_TIMESTAMP);
        proposed := to_jsonb(NEW);
    END IF;
    FOREACH field_name IN ARRAY ARRAY['created_at','updated_at','accepted_at','verified_at','started_at','ended_at','last_heartbeat_at'] LOOP
        IF proposed ? field_name THEN
            IF field_name = 'created_at' THEN
                proposed := jsonb_set(proposed, ARRAY[field_name],
                    CASE WHEN TG_OP = 'INSERT' THEN to_jsonb(CURRENT_TIMESTAMP) ELSE previous->field_name END);
            ELSIF field_name = 'updated_at' THEN
                proposed := jsonb_set(proposed, ARRAY[field_name], to_jsonb(CURRENT_TIMESTAMP));
            ELSIF proposed->field_name <> 'null'::jsonb AND
                  (TG_OP = 'INSERT' OR proposed->field_name IS DISTINCT FROM previous->field_name) THEN
                proposed := jsonb_set(proposed, ARRAY[field_name], to_jsonb(CURRENT_TIMESTAMP));
            END IF;
        END IF;
    END LOOP;
    NEW := jsonb_populate_record(NEW, proposed);
    RETURN NEW;
END $$;

CREATE FUNCTION public.hermes_audit_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE changed jsonb; before_state text; after_state text; aggregate_version integer;
BEGIN
    SELECT COALESCE(jsonb_agg(key ORDER BY key), '[]'::jsonb) INTO changed
    FROM jsonb_each(to_jsonb(NEW))
    WHERE TG_OP = 'INSERT' OR value IS DISTINCT FROM to_jsonb(OLD)->key;
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

CREATE FUNCTION public.hermes_audit_publication() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    UPDATE public.coordination_entities SET version = version + 1 WHERE id = NEW.id;
    INSERT INTO public.audit_events(entity_type, entity_id, actor, action, payload, correlation_id)
    VALUES ('outbox_events', NEW.id, public.hermes_actor(), 'publication.changed',
        jsonb_build_object('previous_status', OLD.publication_status, 'new_status', NEW.publication_status), NEW.id);
    RETURN NEW;
END $$;

CREATE FUNCTION public.hermes_task_consistency() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE task public.tasks%ROWTYPE;
BEGIN
    SELECT * INTO task FROM public.tasks WHERE id = CASE WHEN TG_TABLE_NAME = 'tasks' THEN NEW.id
        ELSE (to_jsonb(NEW)->>'task_id')::uuid END;
    IF task.state IN ('assigned','running') AND NOT EXISTS (
        SELECT 1 FROM public.task_attempts attempt JOIN public.assignments assignment ON assignment.id = attempt.assignment_id
        WHERE attempt.id = task.current_attempt_id AND attempt.task_id = task.id
        AND assignment.state = 'active' AND attempt.state::text = task.state::text
    ) THEN RAISE EXCEPTION 'Executable task lacks matching active assignment/attempt'; END IF;
    IF task.state NOT IN ('assigned','running') AND EXISTS (
        SELECT 1 FROM public.assignments WHERE task_id = task.id AND state = 'active'
    ) THEN RAISE EXCEPTION 'Non-executable task retains active assignment'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.hermes_proposal_consistency() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE proposal public.work_proposals%ROWTYPE;
BEGIN
    SELECT * INTO proposal FROM public.work_proposals WHERE id = NEW.id;
    IF proposal.state IN ('accepted','rejected','expired') AND NOT EXISTS (
        SELECT 1 FROM public.proposal_decisions WHERE proposal_id = proposal.id AND proposal_revision = proposal.revision
        AND decision::text = CASE proposal.state WHEN 'accepted' THEN 'accept' WHEN 'rejected' THEN 'reject' ELSE 'expire' END
    ) THEN RAISE EXCEPTION 'Proposal terminal state requires matching decision'; END IF;
    IF proposal.state = 'accepted' AND NOT EXISTS (SELECT 1 FROM public.proposal_tasks WHERE proposal_id = proposal.id) THEN
        RAISE EXCEPTION 'Proposal acceptance must atomically create linked tasks';
    END IF;
    RETURN NULL;
END $$;
"""


OWNERSHIP_FUNCTIONS = r"""
CREATE FUNCTION public.hermes_owned_attempt(p_attempt uuid, p_token uuid) RETURNS public.task_attempts
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE attempt public.task_attempts%ROWTYPE; task public.tasks%ROWTYPE; assigned public.assignments%ROWTYPE;
BEGIN
    SELECT task_row.* INTO task FROM public.tasks task_row JOIN public.task_attempts attempt_row ON attempt_row.task_id = task_row.id
    WHERE attempt_row.id = p_attempt FOR UPDATE OF task_row;
    SELECT * INTO attempt FROM public.task_attempts WHERE id = p_attempt FOR UPDATE;
    SELECT * INTO assigned FROM public.assignments WHERE id = attempt.assignment_id FOR UPDATE;
    IF p_token IS NULL OR attempt.id IS NULL OR attempt.ownership_token IS DISTINCT FROM p_token OR task.current_attempt_id IS DISTINCT FROM attempt.id
       OR assigned.state <> 'active' OR attempt.state NOT IN ('assigned','running')
       OR attempt.lease_expires_at <= clock_timestamp() OR NOT EXISTS (
           SELECT 1 FROM public.agent_instances instance JOIN public.principals principal ON principal.node_id = instance.node_id
           WHERE instance.id = assigned.instance_id AND principal.id = public.hermes_actor()
           AND principal.kind = 'node' AND instance.state IN ('ready','draining')
       ) THEN RAISE EXCEPTION 'Missing, expired, or foreign attempt ownership' USING ERRCODE = '42501'; END IF;
    RETURN attempt;
END $$;

CREATE FUNCTION public.hermes_claim_attempt(p_attempt uuid, p_token uuid, p_lease_seconds integer) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE attempt public.task_attempts%ROWTYPE;
BEGIN
    IF p_lease_seconds NOT BETWEEN 1 AND 3600 THEN RAISE EXCEPTION 'Invalid lease duration'; END IF;
    attempt := public.hermes_owned_attempt(p_attempt, p_token);
    IF attempt.state <> 'assigned' THEN RETURN false; END IF;
    IF EXISTS (SELECT 1 FROM public.tasks WHERE id = attempt.task_id AND
        (cancellation_intent OR available_at > CURRENT_TIMESTAMP OR deadline_at <= clock_timestamp())) THEN RETURN false; END IF;
    UPDATE public.task_attempts SET state = 'running', started_at = CURRENT_TIMESTAMP,
        lease_expires_at = CURRENT_TIMESTAMP + make_interval(secs => p_lease_seconds) WHERE id = attempt.id;
    UPDATE public.tasks SET state = 'running' WHERE id = attempt.task_id;
    RETURN true;
END $$;

CREATE FUNCTION public.hermes_renew_attempt(p_attempt uuid, p_token uuid, p_lease_seconds integer) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE attempt public.task_attempts%ROWTYPE;
BEGIN
    IF p_lease_seconds NOT BETWEEN 1 AND 3600 THEN RAISE EXCEPTION 'Invalid lease duration'; END IF;
    attempt := public.hermes_owned_attempt(p_attempt, p_token);
    IF attempt.state <> 'running' THEN RETURN false; END IF;
    UPDATE public.task_attempts SET lease_expires_at = CURRENT_TIMESTAMP + make_interval(secs => p_lease_seconds)
    WHERE id = attempt.id;
    RETURN true;
END $$;

CREATE FUNCTION public.hermes_complete_attempt(p_attempt uuid, p_token uuid, p_outcome public."AcceptedOutcome",
    p_summary text, p_data jsonb, p_artifacts uuid[], p_error text DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE attempt public.task_attempts%ROWTYPE; result_id uuid; artifact_id uuid;
BEGIN
    attempt := public.hermes_owned_attempt(p_attempt, p_token);
    IF attempt.state <> 'running' THEN RAISE EXCEPTION 'Attempt is not running'; END IF;
    INSERT INTO public.task_results(attempt_id, accepted_outcome, summary, coordination_data)
    VALUES (p_attempt, p_outcome, p_summary, p_data) RETURNING id INTO result_id;
    FOREACH artifact_id IN ARRAY p_artifacts LOOP
        IF NOT EXISTS (SELECT 1 FROM public.artifact_metadata WHERE id = artifact_id AND state = 'accepted'
            AND producing_attempt_id = p_attempt) THEN RAISE EXCEPTION 'Unaccepted or foreign result artifact'; END IF;
        INSERT INTO public.task_result_artifacts(result_id, artifact_id) VALUES (result_id, artifact_id);
    END LOOP;
    UPDATE public.task_attempts SET state = p_outcome::text::public."AttemptState", ended_at = CURRENT_TIMESTAMP,
        error_classification = p_error WHERE id = p_attempt;
    UPDATE public.tasks SET state = p_outcome::text::public."TaskState" WHERE id = attempt.task_id;
    UPDATE public.assignments SET state = 'completed' WHERE id = attempt.assignment_id;
    RETURN result_id;
END $$;

CREATE FUNCTION public.hermes_heartbeat(p_instance uuid DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE target_node uuid;
BEGIN
    SELECT node_id INTO target_node FROM public.principals WHERE id = public.hermes_actor() AND kind = 'node';
    IF target_node IS NULL THEN RAISE EXCEPTION 'Node identity required'; END IF;
    UPDATE public.nodes SET last_heartbeat_at = CURRENT_TIMESTAMP WHERE id = target_node;
    IF p_instance IS NOT NULL THEN
        UPDATE public.agent_instances SET last_heartbeat_at = CURRENT_TIMESTAMP WHERE id = p_instance AND node_id = target_node;
        IF NOT FOUND THEN RAISE EXCEPTION 'Foreign instance'; END IF;
    END IF;
    RETURN true;
END $$;

CREATE FUNCTION public.hermes_register_instance(p_agent uuid, p_version text, p_digest text) RETURNS uuid
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
    INSERT INTO public.agent_instances(agent_id, node_id, hermes_version, verified_profile_digest)
    VALUES (p_agent, target_node, p_version, p_digest) RETURNING id INTO instance_id;
    RETURN instance_id;
END $$;

CREATE FUNCTION public.hermes_set_instance_state(p_instance uuid, p_state public."InstanceState", p_readiness public."ReadinessState") RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_state NOT IN ('ready','draining','stopped') THEN RAISE EXCEPTION 'Invalid node-reported state'; END IF;
    UPDATE public.agent_instances SET state = p_state, readiness = p_readiness
    WHERE id = p_instance AND node_id = (SELECT node_id FROM public.principals WHERE id = public.hermes_actor() AND kind = 'node');
    IF NOT FOUND THEN RAISE EXCEPTION 'Foreign instance'; END IF;
    RETURN true;
END $$;

CREATE FUNCTION public.hermes_write_owned_record(p_table text, p_attempt uuid, p_token uuid, p_data jsonb) RETURNS uuid
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
            (p_data->>'predecessor_id')::uuid)
        RETURNING id INTO record_id;
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
            AND request.task_id = attempt.task_id AND request.attempt_id = p_attempt
        RETURNING id INTO record_id;
        IF record_id IS NULL THEN RAISE EXCEPTION 'Approval belongs to a different attempt'; END IF;
    ELSE RAISE EXCEPTION 'Unsupported owned write'; END IF;
    RETURN record_id;
END $$;

CREATE FUNCTION public.hermes_finish_execution(p_attempt uuid, p_token uuid, p_session text, p_status public."ExecutionStatus", p_usage jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.hermes_owned_attempt(p_attempt, p_token);
    UPDATE public.execution_runs SET hermes_session_id = p_session, terminal_status = p_status,
        usage_metadata = p_usage, ended_at = CURRENT_TIMESTAMP WHERE attempt_id = p_attempt AND terminal_status IS NULL;
    IF NOT FOUND THEN RAISE EXCEPTION 'Missing or already terminal execution run'; END IF;
    RETURN true;
END $$;

CREATE FUNCTION public.hermes_accept_artifact(p_artifact uuid, p_attempt uuid, p_token uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.hermes_owned_attempt(p_attempt, p_token);
    UPDATE public.artifact_metadata SET state = 'accepted', accepted_at = CURRENT_TIMESTAMP
    WHERE id = p_artifact AND producing_attempt_id = p_attempt AND state = 'verified';
    IF NOT FOUND THEN RAISE EXCEPTION 'Artifact is foreign or not verified'; END IF;
    RETURN true;
END $$;

CREATE FUNCTION public.hermes_set_effect_status(p_effect uuid, p_attempt uuid, p_token uuid,
    p_status public."EffectStatus", p_reference text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.hermes_owned_attempt(p_attempt, p_token);
    UPDATE public.effect_intents SET execution_status = p_status, external_reference = p_reference
    WHERE id = p_effect AND attempt_id = p_attempt;
    IF NOT FOUND THEN RAISE EXCEPTION 'Foreign effect'; END IF;
    RETURN true;
END $$;
"""


PERMISSIONS = r"""
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO hermes_admin, hermes_orchestrator, hermes_node, hermes_intake,
    hermes_approval, hermes_outbox, hermes_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hermes_admin, hermes_orchestrator, hermes_node,
    hermes_intake, hermes_approval, hermes_reader;
GRANT INSERT ON public.nodes, public.agents, public.principals, public.capability_definitions,
    public.instance_capabilities, public.approval_policies TO hermes_admin;
GRANT UPDATE ON public.nodes, public.agents, public.agent_instances, public.approval_policies TO hermes_admin;
GRANT UPDATE(state, retention_policy, retention_expires_at, retention_hold, updated_at)
    ON public.artifact_metadata TO hermes_admin;
GRANT EXECUTE ON FUNCTION public.digest(text,text) TO hermes_admin;
GRANT INSERT ON public.requests, public.artifact_metadata TO hermes_intake;
GRANT INSERT, UPDATE ON public.workflows, public.tasks, public.assignments, public.task_attempts,
    public.work_proposals, public.approval_requests TO hermes_orchestrator;
GRANT INSERT ON public.task_input_references, public.task_capabilities, public.task_dependencies,
    public.proposal_tasks, public.proposal_artifacts, public.proposal_decisions, public.approval_request_artifacts TO hermes_orchestrator;
GRANT UPDATE ON public.agent_instances TO hermes_orchestrator;
GRANT INSERT ON public.approval_decisions TO hermes_approval;
GRANT UPDATE(state, updated_at) ON public.approval_requests TO hermes_approval;
GRANT INSERT, UPDATE ON public.effect_intents TO hermes_orchestrator;
GRANT SELECT ON public.outbox_events, public.coordination_entities TO hermes_outbox;
GRANT UPDATE(publication_status, publisher_lease, publisher_lease_expires_at, retry_at)
    ON public.outbox_events TO hermes_outbox;
GRANT EXECUTE ON FUNCTION public.hermes_claim_attempt(uuid,uuid,integer),
    public.hermes_renew_attempt(uuid,uuid,integer),
    public.hermes_complete_attempt(uuid,uuid,public."AcceptedOutcome",text,jsonb,uuid[],text),
    public.hermes_heartbeat(uuid), public.hermes_register_instance(uuid,text,text),
    public.hermes_set_instance_state(uuid,public."InstanceState",public."ReadinessState"),
    public.hermes_write_owned_record(text,uuid,uuid,jsonb),
    public.hermes_finish_execution(uuid,uuid,text,public."ExecutionStatus",jsonb),
    public.hermes_accept_artifact(uuid,uuid,uuid),
    public.hermes_set_effect_status(uuid,uuid,uuid,public."EffectStatus",text) TO hermes_node;
"""