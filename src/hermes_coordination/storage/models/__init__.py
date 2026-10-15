from .base import Base
from .identities import CoordinationEntity, Principal
from .nodes import Node
from .agents import Agent, AgentInstance
from .capabilities import CapabilityDefinition, InstanceCapability
from .requests import Request
from .workflows import Workflow
from .tasks import Task, TaskCapability, TaskDependency, TaskInputReference
from .assignments import Assignment
from .attempts import TaskAttempt
from .execution import ExecutionRun, TaskResult, TaskResultArtifact
from .proposals import WorkProposal, ProposalTask, ProposalDecisionRecord, ProposalArtifact
from .approvals import ApprovalPolicy, ApprovalRequest, ApprovalRequestArtifact, ApprovalDecisionRecord, EffectIntent
from .artifacts import ArtifactMetadata, ArtifactLineage
from .events import TaskEvent, AuditEvent
from .outbox import OutboxEvent


__all__ = [
    "Base", "CoordinationEntity", "Principal", "Node", "Agent", "AgentInstance",
    "CapabilityDefinition", "InstanceCapability", "Request", "Workflow", "Task",
    "TaskCapability", "TaskDependency", "TaskInputReference", "Assignment", "TaskAttempt",
    "ExecutionRun", "TaskResult", "TaskResultArtifact", "WorkProposal", "ProposalTask",
    "ProposalDecisionRecord", "ProposalArtifact", "ApprovalPolicy", "ApprovalRequest", "ApprovalRequestArtifact",
    "ApprovalDecisionRecord", "EffectIntent", "ArtifactMetadata", "ArtifactLineage",
    "TaskEvent", "AuditEvent", "OutboxEvent",
]

for table in Base.metadata.tables.values():
    for constraint in table.foreign_key_constraints:
        constraint.deferrable = True
        constraint.initially = "DEFERRED"