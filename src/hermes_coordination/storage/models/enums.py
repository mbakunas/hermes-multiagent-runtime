from enum import StrEnum


class NodeState(StrEnum):
    ENROLLED = "enrolled"
    ENABLED = "enabled"
    DRAINING = "draining"
    DISABLED = "disabled"
    RETIRED = "retired"


class AgentState(StrEnum):
    PROVISIONED = "provisioned"
    ENABLED = "enabled"
    DISABLED = "disabled"
    RETIRED = "retired"


class AgentRole(StrEnum):
    CONCIERGE = "concierge"
    ORCHESTRATOR = "orchestrator"
    RESEARCHER = "researcher"
    LIBRARIAN = "librarian"
    CORRESPONDENT = "correspondent"
    CODER = "coder"


class InstanceState(StrEnum):
    REGISTERING = "registering"
    READY = "ready"
    DRAINING = "draining"
    STOPPED = "stopped"
    SUSPECT = "suspect"
    OFFLINE = "offline"


class ReadinessState(StrEnum):
    NOT_READY = "not_ready"
    READY = "ready"
    DEGRADED = "degraded"


class RequestStatus(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskState(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"
    READY = "ready"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AssignmentState(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    REVOKED = "revoked"


class AttemptState(StrEnum):
    ASSIGNED = "assigned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    WAITING_APPROVAL = "waiting_approval"


class RequirementKind(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    PREFERRED = "preferred"


class ProposalState(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REVOKED = "revoked"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ArtifactState(StrEnum):
    STAGED = "staged"
    VERIFIED = "verified"
    ACCEPTED = "accepted"
    RETIRED = "retired"
    DELETION_PENDING = "deletion_pending"
    DELETED = "deleted"
    QUARANTINED = "quarantined"


class OutboxPublicationStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"


class PrincipalKind(StrEnum):
    USER = "user"
    SERVICE = "service"
    NODE = "node"


class PolicyActivationState(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class EffectStatus(StrEnum):
    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class DependencyCondition(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TERMINAL = "terminal"


class ProposalDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EXPIRE = "expire"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    UNKNOWN = "unknown"


class AcceptedOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ENUM_TYPES = (
    NodeState, AgentState, AgentRole, InstanceState, ReadinessState,
    RequestStatus, WorkflowStatus, TaskState, AssignmentState, AttemptState,
    RequirementKind, ProposalState, ApprovalState, ApprovalDecision,
    ArtifactState, OutboxPublicationStatus, PrincipalKind,
    PolicyActivationState, EffectStatus, DependencyCondition,
    ProposalDecision, ExecutionStatus, AcceptedOutcome,
)