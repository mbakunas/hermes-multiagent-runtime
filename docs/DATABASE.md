# Phase 1 PostgreSQL Implementation

Implementation reference for the frozen [Architecture v1.0](../ARCHITECTURE.md). The architecture itself is unchanged.

Target: Python 3.13, PostgreSQL 17+, SQLAlchemy 2.0 typed ORM, Alembic, and psycopg 3. This package contains persistence only, not agents, scheduling policy, execution supervision, storage of artifact bytes, or MQTT clients.

## Implementation Layout

- [Models](../src/hermes_coordination/storage/models/__init__.py): all 25 requested first-class tables, plus seven integrity-support tables, for 32 domain tables total.
- [Enums](../src/hermes_coordination/storage/models/enums.py): the required enums plus role, dependency, decision, effect, and execution enums.
- [Initial migration](../src/hermes_coordination/storage/migrations/versions/initial_schema.py): frozen definitions, all foreign keys/indexes, pgcrypto, role grants, guards, ownership functions, audit/outbox triggers, and the 19 initial capability definitions.
- [Database configuration](../src/hermes_coordination/storage/db.py): UTC sessions, PostgreSQL version validation, bounded pools/timeouts, and caller-owned transactions.
- [Repositories](../src/hermes_coordination/storage/repositories/__init__.py): SQLAlchemy 2 persistence queries and guarded database calls; no implicit commits, task selection policy, retry scheduling, or destructive delete API.
- [Contract tests](../tests/test_schema_contracts.py) and [database tests](../tests/test_postgresql_integrity.py): deployment-side validation, not executed on the development workstation.

## Deployment And Validation

Run these commands on the deployment/validation host, from the repository root, in an existing or deliberately provisioned Python 3.13 environment. No local workstation environment is required.

```sh
python -m pip install -e '.[test]'
alembic -c src/hermes_coordination/storage/migrations/alembic.ini upgrade head
python -m pytest tests/test_schema_contracts.py
python -m hermes_coordination.storage.inventory
```

Supply `HERMES_DATABASE_URL` through the host's protected service configuration, using `postgresql+psycopg` and the stable PostgreSQL DNS alias. Do not commit credentials or copy migration-owner credentials into Node Services. Configure the deployment environment through the transaction-local `hermes.environment` setting; the default topic environment is `default`.

The initial revision assumes a dedicated database whose `public` schema has no conflicting application objects. The migration owner must own the database/schema and be authorized to install pgcrypto and create/manage the named NOLOGIN roles. Pre-provision the extension/roles through a DBA when local deployment policy requires it. Do not run migrations as an application login. The migration revokes public schema creation, public table access, and public execution of functions in this dedicated schema.

Set `HERMES_TEST_DATABASE_URL` to an **empty disposable PostgreSQL 17+ database**, using a migration-capable owner, then run:

```sh
python -m pytest tests/test_postgresql_integrity.py
```

Integration tests refuse a nonempty public schema, install the initial migration, and roll back individual test data. They leave the disposable schema installed and do not execute a destructive downgrade. PostgreSQL roles are cluster-wide and are intentionally not dropped by tests. Use a dedicated validation cluster/database and do not use production credentials or data. These tests are a baseline; real service-login ownership and concurrent failover tests remain deployment release gates.

The migration can emit offline SQL using Alembic's `upgrade head --sql` mode. Apply it transactionally as the migration owner. Destructive downgrade is intentionally prohibited; recovery restores a verified backup into a separate database. The migration contains frozen schema definitions and never imports live ORM models. The Alembic bookkeeping table is Alembic-managed and is not one of the domain UUID tables.

## Transaction And Authority Contract

Use `Database.transaction()` for short persistence transactions. Do not keep a transaction open while Hermes Agent executes, MQTT publishes, or NAS uploads occur. PostgreSQL generates lifecycle timestamps using `CURRENT_TIMESTAMP`; deadlines/availability and lease/retry offsets are scheduling data, computed using PostgreSQL expressions or supplied absolute UTC contract values, not Python clock calls. `clock_timestamp()` is used only for live expiry comparisons so a transaction-start timestamp cannot authorize an already-expired lease.

Every domain primary key is `UUID(as_uuid=True)` with `gen_random_uuid()` as its server default. BEFORE triggers reject changes to primary keys. The entity registry is created transactionally by insertion triggers, is not directly writable by applications, and gives audit/outbox references a real foreign-key target and monotonically increasing entity version. It does not introduce another task store or authority.

All foreign keys are `ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED`. This supports the task/current-attempt cycle and atomic assignment/completion transactions. Check and partial uniqueness constraints are immediate. Services must commit a coherent final state; deferred consistency triggers validate task/assignment/attempt alignment and proposal acceptance. No application role can delete or truncate domain history; append-only records also reject updates.

| Database group role | Write authority |
| --- | --- |
| `hermes_admin` | Provision nodes, agents, principals, capability definitions/inventories and approval policies; manage registry desired state, retire policies, and administer artifact retention metadata |
| `hermes_orchestrator` | Create tasks/workflows, requirements/dependencies, assignments/attempts; decide proposals and link resulting tasks; reconcile approvals/effects |
| `hermes_node` | No direct coordination table writes; execute owned claim/renew/completion, registration, heartbeat, execution, artifact, proposal, approval-request, and effect functions |
| `hermes_intake` | Insert authenticated requests and verified external-input artifact metadata |
| `hermes_approval` | Insert durable human decisions and update the corresponding approval request state |
| `hermes_outbox` | Read outbox state and update only publication status, publisher token/expiry, and retry time |
| `hermes_reader` | Read-only reporting access |

Provision a distinct ordinary LOGIN for each service/node, grant only its group membership, and register its exact `database_role` in `principals`. Node principals bind to a real node FK. Do not share a node login between hosts. Security-definer operations use a fixed search path and the authenticated `session_user`, not a caller-supplied actor or writable identity setting. Functions are not executable by PUBLIC. Database owners/superusers remain trusted DBA authorities and can bypass PostgreSQL security mechanisms; never use them for applications.

Trusted infrastructure logins can read coordination metadata; end-user and worker data scopes must still be enforced by their service/bridge. Workers never receive these credentials. The approval API must authenticate the human and verify `required_approver_role`; the database verifies the registered human FK, revision uniqueness, and durable state/action binding. A browser message or MQTT event is not a decision.

Assignments require a ready task, enabled node/agent, ready instance, approved profile digest, and registered required capability IDs. In Phase 1, `approved_profile_revision` stores the approved profile digest used for exact registration/assignment verification; a friendly release label is not substituted for that digest. Complete version-constraint compatibility, heartbeat freshness, optional/preferred scoring, dependencies, quotas, and policy remain Orchestrator responsibilities. The repository `ready()` query is only a persistence candidate query, not a scheduler.

One active incarnation is enforced for `registering`, `ready`, `draining`, and `suspect` instances, conservatively preventing restart overlap. Capacity is exactly one in Phase 1. One active assignment per task **and per instance**, plus one active attempt per task, enforce that slot. One assignment represents one attempt; a retry creates new records rather than reopening history.

Ownership functions lock task, attempt, then assignment; validate current token, lease, assignment and node/instance; and conditionally mutate only that scope. Random UUID tokens are basic database fencing, not Phase 3 monotonic external-effect fencing. A sensitive effect requires a valid durable approval and live ownership. Unknown effects are reconciled, never redispatched automatically. Schema checks cannot prove a process stopped or an external effect did not occur.

The node service must persist a stopped execution outcome before suspending its attempt for approval. Waiting approval closes that attempt/assignment. Continuation is an Orchestrator-created new attempt; old decisions do not implicitly transfer to it. A new action-bound approval request is required when rebinding to a new attempt. This package does not implement the continuation planner.

An approval service inserts `ApprovalDecisionRecord`, flushes, then changes the matching pending request to the appropriate state in the **same transaction**. The row lock serializes decisions with approval artifact binding and effects. Proposal acceptance likewise writes decision, generated tasks, links, and final accepted state in one transaction; a deferred trigger rejects acceptance without both decision and linked tasks.

State-changing persistence automatically creates sanitized audit and outbox records in the same transaction. Audit payloads record changed column names and previous/new state rather than copying prompts or full contracts. Task changes also create task events. Outbox delivery transitions have their own append-only audit trigger, without recursively enqueueing more delivery events. The original outbox entity/envelope/topic are immutable; polling and lease recovery remain essential.

## ERD

Each table shown has an immutable UUID primary key. Every domain table except `coordination_entities` also has a PK-to-registry FK; those repetitive identity edges are summarized rather than drawn 31 times.

```mermaid
erDiagram
    nodes ||--o{ principals : node_binding
    nodes ||--o{ agent_instances : hosts
    agents ||--o{ agent_instances : incarnations
    agents ||--o{ requests : concierge
    principals ||--o{ requests : initiates
    artifact_metadata ||--o{ requests : intent
    agent_instances ||--o{ instance_capabilities : inventory
    capability_definitions ||--o{ instance_capabilities : defines
    requests ||--o{ workflows : creates
    workflows ||--o{ tasks : contains
    tasks ||--o{ task_input_references : inputs
    artifact_metadata o|--o{ task_input_references : artifact_input
    tasks ||--o{ task_capabilities : requires
    capability_definitions ||--o{ task_capabilities : resolves
    tasks ||--o{ task_dependencies : prerequisite
    tasks ||--o{ task_dependencies : dependent
    tasks ||--o{ assignments : assigned_work
    agent_instances ||--o{ assignments : owns
    principals ||--o{ assignments : orchestrator
    assignments ||--o| task_attempts : attempt
    tasks ||--o{ task_attempts : attempts
    task_attempts o|--o| tasks : current_attempt
    task_attempts ||--o| execution_runs : execution
    agents ||--o{ execution_runs : profile
    task_attempts ||--o| task_results : result
    task_results ||--o{ task_result_artifacts : accepted_outputs
    artifact_metadata ||--o{ task_result_artifacts : references
    agents ||--o{ work_proposals : proposes
    agent_instances ||--o{ work_proposals : proposing_instance
    tasks ||--o{ work_proposals : originating_task
    task_attempts ||--o{ work_proposals : originating_attempt
    work_proposals o|--o{ work_proposals : predecessor
    work_proposals ||--o{ proposal_artifacts : referenced_inputs
    artifact_metadata ||--o{ proposal_artifacts : proposal_input
    work_proposals ||--o{ proposal_tasks : generated_work
    tasks ||--o{ proposal_tasks : generated_task
    work_proposals ||--o| proposal_decisions : revision_decision
    principals ||--o{ proposal_decisions : reviews
    approval_policies ||--o{ approval_requests : policy_revision
    tasks ||--o{ approval_requests : action_task
    task_attempts ||--o{ approval_requests : action_attempt
    principals ||--o{ approval_requests : requests_approval
    approval_requests ||--o{ approval_request_artifacts : checksum_bindings
    artifact_metadata ||--o{ approval_request_artifacts : approved_bytes
    approval_requests ||--o| approval_decisions : user_decision
    principals ||--o{ approval_decisions : human
    approval_requests ||--o{ effect_intents : authorization
    tasks ||--o{ effect_intents : effect_task
    task_attempts ||--o{ effect_intents : effect_attempt
    tasks o|--o{ artifact_metadata : produces
    task_attempts o|--o{ artifact_metadata : producing_attempt
    principals ||--o{ artifact_metadata : uploader
    artifact_metadata o|--o{ artifact_metadata : supersedes
    artifact_metadata ||--o{ artifact_lineage : derived_artifact
    artifact_metadata ||--o{ artifact_lineage : source_artifact
    tasks ||--o{ task_events : state_history
    principals ||--o{ task_events : actor
    principals ||--o{ audit_events : actor
    coordination_entities ||--o{ audit_events : audited_entity
    coordination_entities ||--o{ outbox_events : signaled_entity
    coordination_entities ||--o{ task_events : correlation_causation
    coordination_entities ||--o{ audit_events : correlation_causation
```

## Index Inventory

Every domain table has the primary-key index `pk_{table}`. Every UNIQUE constraint below also creates its PostgreSQL unique index. All additional indexes are listed here; comma-separated names on a row are independent indexes, not one composite index. The inventory utility prints exact PostgreSQL DDL including identifier truncation where necessary.

| Table | Additional indexes |
| --- | --- |
| `coordination_entities`, `nodes`, `agents`, `capability_definitions`, `task_results`, `approval_policies` | No additional non-constraint indexes |
| `principals` | `ix_principals_node_id` |
| `agent_instances` | `ix_agent_instances_agent_id`, `ix_agent_instances_node_id`, `ix_agent_instances_eligibility(state, readiness, last_heartbeat_at)`; `uq_agent_instances_active_incarnation(agent_id)` WHERE state IN registering/ready/draining/suspect |
| `instance_capabilities` | `ix_instance_capabilities_instance_id`, `ix_instance_capabilities_capability_id` |
| `requests` | `ix_requests_authenticated_initiator`, `ix_requests_concierge_reference`, `ix_requests_intent_reference` |
| `workflows` | `ix_workflows_request_id` |
| `tasks` | `ix_tasks_workflow_id`, `ix_tasks_deadline_at`, `ix_tasks_current_attempt_id`, `ix_tasks_schedule(state, available_at, deadline_at)` |
| `task_input_references` | `ix_task_input_references_task_id`, `ix_task_input_references_artifact_id` |
| `task_capabilities` | `ix_task_capabilities_task_id`, `ix_task_capabilities_capability_id` |
| `task_dependencies` | `ix_task_dependencies_prerequisite_task_id`, `ix_task_dependencies_dependent_task_id` |
| `assignments` | `ix_assignments_task_id`, `ix_assignments_instance_id`, `ix_assignments_authoritative_assigner`; `uq_assignments_active_task(task_id)` and `uq_assignments_active_instance(instance_id)` WHERE state = active |
| `task_attempts` | `ix_task_attempts_task_id`; `uq_task_attempts_active_task(task_id)` and `ix_task_attempts_leases(lease_expires_at)` WHERE state IN assigned/running |
| `execution_runs` | `ix_execution_runs_profile_identity` |
| `task_result_artifacts` | `ix_task_result_artifacts_result_id`, `ix_task_result_artifacts_artifact_id` |
| `work_proposals` | `ix_work_proposals_proposing_instance_id`, `ix_work_proposals_originating_attempt_id`, `ix_work_proposals_originating_task_id`, `ix_work_proposals_proposing_agent_id`, `ix_work_proposals_predecessor_id`, `ix_work_proposals_review(state, expiry_at)` |
| `proposal_artifacts` | `ix_proposal_artifacts_proposal_id`, `ix_proposal_artifacts_artifact_id` |
| `proposal_tasks` | `ix_proposal_tasks_proposal_id`, `ix_proposal_tasks_task_id` |
| `proposal_decisions` | `ix_proposal_decisions_proposal_id`, `ix_proposal_decisions_orchestrator_principal` |
| `approval_requests` | `ix_approval_requests_attempt_id`, `ix_approval_requests_task_id`, `ix_approval_requests_policy_id`, `ix_approval_requests_requesting_principal`; `ix_approval_requests_pending(expires_at)` WHERE state = pending |
| `approval_request_artifacts` | `ix_approval_request_artifacts_request_id`, `ix_approval_request_artifacts_artifact_id` |
| `approval_decisions` | `ix_approval_decisions_request_id`, `ix_approval_decisions_authenticated_user` |
| `effect_intents` | `ix_effect_intents_attempt_id`, `ix_effect_intents_task_id`, `ix_effect_intents_approval_request_id` |
| `artifact_metadata` | `ix_artifact_metadata_producing_task_id`, `ix_artifact_metadata_producing_attempt_id`, `ix_artifact_metadata_uploader_identity`, `ix_artifact_metadata_supersedes_id`, `ix_artifact_metadata_retention(state, retention_expires_at)` |
| `artifact_lineage` | `ix_artifact_lineage_artifact_id`, `ix_artifact_lineage_source_artifact_id` |
| `task_events` | `ix_task_events_task_id`, `ix_task_events_actor`, `ix_task_events_correlation_id`, `ix_task_events_causation_id` |
| `audit_events` | `ix_audit_events_actor`, `ix_audit_events_correlation_id`, `ix_audit_events_causation_id`, `ix_audit_events_entity(entity_id, created_at)` |
| `outbox_events` | `ix_outbox_events_publication_status`, `ix_outbox_events_retry_at`, `ix_outbox_events_entity_id`, `ix_outbox_events_publisher_lease_expires_at`; `ix_outbox_events_pending(retry_at, created_at)` WHERE publication_status != published |

## Constraint Inventory

Common to all domain tables: immutable UUID `pk_{table}`; NOT NULL as declared in the typed model/frozen migration; and, except the registry, `fk_{table}_id_coordination_entities`. All relationships listed in the ERD are backed by actual FKs. Foreign-key names use `fk_{table}_{first_local_column}_{parent}`, except `fk_tasks_current_attempt`. UNIQUE names use `uq_{table}_{all_columns}`; CHECK names use `ck_{table}_{label}`. Exact names/definitions are emitted by the inventory utility; no relationship relies on a JSON UUID array or string-based ORM join.

### All UNIQUE Constraints

| Table | Unique column sets |
| --- | --- |
| `coordination_entities` | `(id, entity_type)` |
| `principals` | `(subject)`, `(database_role)` |
| `nodes` | `(name)`, `(enrollment_identity)` |
| `agents` | `(name)` |
| `agent_instances` | `(id, agent_id)` |
| `capability_definitions` | `(canonical_name, contract_version)`, `(id, canonical_name)` |
| `instance_capabilities` | `(instance_id, capability_id)` |
| `requests` | `(idempotency_key)` |
| `tasks` | `(id, workflow_id)` |
| `task_input_references` | `(task_id, artifact_id)`, `(task_id, external_reference)` |
| `task_capabilities` | `(task_id, capability_name)` |
| `task_dependencies` | `(prerequisite_task_id, dependent_task_id)` |
| `assignments` | `(id, task_id)` |
| `task_attempts` | `(task_id, attempt_number)`, `(id, task_id)`, `(assignment_id)`, `(ownership_token)` |
| `execution_runs` | `(attempt_id)`, `(node_execution_id)` |
| `task_results` | `(attempt_id)` |
| `task_result_artifacts` | `(result_id, artifact_id)` |
| `work_proposals` | `(id, revision)`, `(submission_idempotency_key)` |
| `proposal_artifacts` | `(proposal_id, artifact_id)` |
| `proposal_tasks` | `(proposal_id, task_id)` |
| `proposal_decisions` | `(proposal_id, proposal_revision)` |
| `approval_policies` | `(action_category, revision)`, `(id, revision)` |
| `approval_requests` | `(id, revision)`, `(id, task_id, attempt_id, action_digest)` |
| `approval_request_artifacts` | `(request_id, artifact_id)` |
| `approval_decisions` | `(request_id, request_revision)`, `(idempotency_key)` |
| `effect_intents` | `(business_idempotency_key)` |
| `artifact_metadata` | `(id, checksum_digest)`, `(provider, export_identifier, environment, storage_key)` |
| `artifact_lineage` | `(artifact_id, source_artifact_id)` |

Other tables have no non-PK UNIQUE constraints. The four partial unique-index definitions are in the index inventory: incarnation, active task assignment, active instance assignment, and active task attempt.

### All CHECK Constraints

- `coordination_entities.version`, `workflows.version`, `tasks.version`: strictly positive.
- `principals.node_binding`: node kind if and only if a node FK is present.
- `agent_instances.phase_one_capacity`: capacity exactly one.
- `capability_definitions.canonical_name`: lowercase snake_case; `positive_version`: positive contract version.
- `tasks.deadline`: deadline absent or not earlier than availability.
- `task_input_references.one_source`: exactly one artifact FK or external resource reference.
- `task_dependencies.not_self`, `artifact_lineage.not_self`: no self-edge.
- `task_attempts.positive_attempt`: attempt number positive; `time_order`: end not earlier than start.
- `work_proposals.revision`, `approval_policies.revision`, `approval_requests.revision`: positive revision.
- `work_proposals.payload_digest`, `approval_policies.policy_digest`, `approval_requests.action_digest`: lowercase 64-character hexadecimal SHA-256 representation.
- `artifact_metadata.byte_size`: nonnegative; `checksum`: SHA-256 and valid digest; `producing_origin`: both producing FKs present or explicit external intake origin; `phase_one_provider`: NAS only; `relative_key`: relative key without parent traversal.
- `outbox_events.entity_version`: positive; `publisher_ownership`: publishing requires token plus expiry; all other statuses require both NULL.
- Every JSONB column has `{column}_bounded`: expected object shape and at most 65,536 bytes in PostgreSQL text representation; `assignments.capability_snapshot` is an array; outbox envelope limit is 8,192 bytes. Non-nullability prevents NULL from bypassing these checks.

### All Composite Foreign Keys

- `tasks(current_attempt_id, id) -> task_attempts(id, task_id)`.
- `task_capabilities(capability_id, capability_name) -> capability_definitions(id, canonical_name)`.
- `task_attempts(assignment_id, task_id) -> assignments(id, task_id)`.
- `work_proposals(proposing_instance_id, proposing_agent_id) -> agent_instances(id, agent_id)`.
- `work_proposals(originating_attempt_id, originating_task_id) -> task_attempts(id, task_id)`.
- `proposal_decisions(proposal_id, proposal_revision) -> work_proposals(id, revision)`.
- `approval_requests(attempt_id, task_id) -> task_attempts(id, task_id)`.
- `approval_requests(policy_id, policy_revision) -> approval_policies(id, revision)`.
- `approval_request_artifacts(artifact_id, checksum_digest) -> artifact_metadata(id, checksum_digest)`.
- `approval_decisions(request_id, request_revision) -> approval_requests(id, revision)`.
- `effect_intents(attempt_id, task_id) -> task_attempts(id, task_id)`.
- `effect_intents(approval_request_id, task_id, attempt_id, action_digest) -> approval_requests(id, task_id, attempt_id, action_digest)`.
- `artifact_metadata(producing_attempt_id, producing_task_id) -> task_attempts(id, task_id)`.
- `audit_events(entity_id, entity_type)` and `outbox_events(entity_id, entity_type) -> coordination_entities(id, entity_type)`.

All remaining FKs are single-column edges in the ERD, including task/attempt direct task IDs, principals for every authenticated actor, predecessor/superseded artifacts/proposals, and correlation/causation entity IDs. Immutable references use no `CASCADE`, `SET NULL`, or soft-delete rewrite.

### Trigger-Enforced Constraints

- Identity/type immutability; database lifecycle timestamps; task/workflow optimistic version advancement; registry entity-version advancement.
- No DELETE/TRUNCATE; append-only associations, verified inventory revisions, decisions, accepted results, task/audit events, and principal/capability definitions.
- Sole Orchestrator assignment/proposal-decision authority and authenticated assigner matching.
- Current assignment/task/attempt coherence at transaction end; no active assignment on a non-executable task.
- Same-workflow dependency edges and serialized cycle rejection.
- Frozen submitted proposals and decided approval artifact bindings; matching durable terminal proposal decisions and tasks on acceptance.
- Current durable human decision before approval-state advancement; exact effect/task/attempt/digest binding, expiry checks, and protected artifact content/provenance after acceptance.
- Terminal attempt history and ownership token immutability; sensitive effect binding and non-replayable status transitions.
- Outbox envelope immutability; audit of publication transitions without recursive signaling.

## Architectural Concerns And Implementation Resolutions

1. **Polymorphic entity IDs and actors:** A UUID plus `entity_type` alone is not a foreign key. Added `coordination_entities` and `principals` integrity tables, not new runtime subsystems. Identity rows are created by triggers; principals are administrator-provisioned and cannot self-grant authority.
2. **Internal references in JSON:** `tasks.input_references`, `task_results.artifact_references`, proposal artifact references, and approval artifact digests are typed ORM associations, not JSONB UUID lists. Added association tables preserve actual FKs. External URLs/resource names are external contract data, not references to internal database rows. JSON payloads must not be used to bypass these associations.
3. **Capability names are not globally unique across versions:** Added `task_capabilities.capability_id` plus a composite name-matching FK, preserving the requested name/version-constraint fields. Initial catalog version is one; compatibility expression interpretation remains deterministic Orchestrator validation, not repository business logic.
4. **Revision-bound decisions:** Added proposal/request revision FKs and approval policy revision capture; one decision per revision is enforced. Approval action specifications and proposal payload contracts were missing from the requested field lists and are stored as bounded JSONB. Canonical proposal/action digests are computed from PostgreSQL JSONB text, not assumed to equal arbitrary client JSON serialization.
5. **Intake before producing tasks:** Uploaded request intent exists before a producing attempt. Only explicitly marked external intake artifacts permit both producing FKs NULL; generated artifacts always require both. This avoids an impossible request/task/artifact bootstrap cycle while retaining provenance and strict generated-artifact ownership.
6. **Required NAS metadata:** Added export/environment, original filename, verification/acceptance time, retention hold/expiry, superseded artifact FK, and lineage links. The database stores metadata only; it cannot verify NAS bytes or guarantee NAS permissions/backups. Those checks remain the existing artifact infrastructure's deployment responsibility.
7. **Outbox recovery:** Added publisher lease expiry because a token alone cannot recover a crashed publisher. A database acknowledgement checks token and expiry; publication remains at least once. Envelope entity versions are database-ordered even for models without an optimistic ORM version column.
8. **No destructive history:** Immutable inventory rows need a new agent incarnation/profile revision to advertise changed inventory; repositories do not replace/delete old rows. Audit records contain sanitized change metadata, not full historical contract snapshots. Services must use new task/proposal/policy revisions when preserving changed contract content is required.
9. **State machines and services:** Database guards enforce key safety/immutability/ownership boundaries. Full scheduling, budgets, policy scope/approver-role evaluation, proposal semantic validation, NAS verification, and runtime stop confirmation belong to existing coordination services. No claim is made that generating this storage package implements those services.
10. **Approval continuation:** Attempt-bound approval and closing ownership while waiting mean an approval cannot be silently reused on a retry/new attempt. Continuation must create a valid new binding and obtain required authorization; this remains explicit rather than weakening the frozen authority contract.
11. **Identity/authentication provisioning:** Login roles and user subjects are external authenticated identities; services must provision legitimate principals and enforce bridge scopes. PostgreSQL group-role names are cluster-wide. Approval-service credentials are a sensitive trust boundary and must not be exposed to agents or n8n workflows that can forge users.
12. **Validation status:** Editor diagnostics/static file coverage are not import, migration, or PostgreSQL execution tests. The deployment tests included here must pass on Python 3.13/PostgreSQL 17+ before this implementation is considered production-verified. Additional real-login cross-node ownership, concurrent assignment/decision/cycle tests, crash recovery, and schema/permission inspection are required release gates.