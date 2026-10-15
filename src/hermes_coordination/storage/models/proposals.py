from datetime import datetime
from typing import Any
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedMixin, IdentityMixin, TimestampMixin, bounded_json, enum_type
from .enums import ProposalDecision, ProposalState


class WorkProposal(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "work_proposals"
    __table_args__ = (
        ForeignKeyConstraint(["proposing_instance_id", "proposing_agent_id"],
                             ["agent_instances.id", "agent_instances.agent_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["originating_attempt_id", "originating_task_id"],
                             ["task_attempts.id", "task_attempts.task_id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "revision"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="payload_digest"),
        bounded_json("payload_json"),
        Index("ix_work_proposals_review", "state", "expiry_at"),
        Index("ix_work_proposals_proposing_instance_id", "proposing_instance_id"),
        Index("ix_work_proposals_originating_attempt_id", "originating_attempt_id"),
        Index("ix_work_proposals_originating_task_id", "originating_task_id"),
        Index("ix_work_proposals_proposing_agent_id", "proposing_agent_id"),
    )

    proposing_agent_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT")
    )
    proposing_instance_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    originating_task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT")
    )
    originating_attempt_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    proposal_type: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(server_default=text("1"))
    state: Mapped[ProposalState] = mapped_column(enum_type(ProposalState), server_default=text("'draft'"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_digest: Mapped[str] = mapped_column(String(64))
    expiry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submission_idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    predecessor_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_proposals.id", ondelete="RESTRICT"), index=True
    )
    artifact_references: Mapped[list["ProposalArtifact"]] = relationship(
        back_populates="proposal", lazy="raise", cascade="save-update, merge"
    )


class ProposalArtifact(IdentityMixin, Base):
    __tablename__ = "proposal_artifacts"
    __table_args__ = (UniqueConstraint("proposal_id", "artifact_id"),)

    proposal_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_proposals.id", ondelete="RESTRICT"), index=True
    )
    artifact_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_metadata.id", ondelete="RESTRICT"), index=True
    )
    proposal: Mapped[WorkProposal] = relationship(back_populates="artifact_references")


class ProposalTask(IdentityMixin, Base):
    __tablename__ = "proposal_tasks"
    __table_args__ = (UniqueConstraint("proposal_id", "task_id"),)

    proposal_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_proposals.id", ondelete="RESTRICT"), index=True
    )
    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )


class ProposalDecisionRecord(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "proposal_decisions"
    __table_args__ = (
        ForeignKeyConstraint(["proposal_id", "proposal_revision"],
                             ["work_proposals.id", "work_proposals.revision"], ondelete="RESTRICT"),
        UniqueConstraint("proposal_id", "proposal_revision"),
        Index("ix_proposal_decisions_proposal_id", "proposal_id"),
    )

    proposal_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    proposal_revision: Mapped[int]
    orchestrator_principal: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[ProposalDecision] = mapped_column(enum_type(ProposalDecision))
    reason: Mapped[str] = mapped_column(String(4096))
    policy_revision: Mapped[str] = mapped_column(String(128))