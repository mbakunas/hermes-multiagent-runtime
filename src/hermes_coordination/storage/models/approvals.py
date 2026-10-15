from datetime import datetime
from typing import Any
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedMixin, IdentityMixin, TimestampMixin, bounded_json, enum_type
from .enums import ApprovalDecision, ApprovalState, EffectStatus, PolicyActivationState


class ApprovalPolicy(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint("action_category", "revision"),
        UniqueConstraint("id", "revision"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint("policy_digest ~ '^[0-9a-f]{64}$'", name="policy_digest"),
        bounded_json("scope_rules"), bounded_json("expiry_rules"),
    )

    revision: Mapped[int]
    action_category: Mapped[str] = mapped_column(String(128))
    scope_rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    required_approver_role: Mapped[str] = mapped_column(String(128))
    expiry_rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    policy_digest: Mapped[str] = mapped_column(String(64))
    activation_state: Mapped[PolicyActivationState] = mapped_column(enum_type(PolicyActivationState))


class ApprovalRequest(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        ForeignKeyConstraint(["attempt_id", "task_id"], ["task_attempts.id", "task_attempts.task_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["policy_id", "policy_revision"], ["approval_policies.id", "approval_policies.revision"],
                             ondelete="RESTRICT"),
        UniqueConstraint("id", "revision"),
        UniqueConstraint("id", "task_id", "attempt_id", "action_digest"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint("action_digest ~ '^[0-9a-f]{64}$'", name="action_digest"),
        bounded_json("action_specification"),
        Index("ix_approval_requests_pending", "expires_at", postgresql_where=text("state = 'pending'")),
        Index("ix_approval_requests_attempt_id", "attempt_id"),
        Index("ix_approval_requests_task_id", "task_id"),
        Index("ix_approval_requests_policy_id", "policy_id"),
    )

    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT")
    )
    attempt_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    policy_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    policy_revision: Mapped[int]
    revision: Mapped[int] = mapped_column(server_default=text("1"))
    action_digest: Mapped[str] = mapped_column(String(64))
    action_specification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    artifact_digests: Mapped[list["ApprovalRequestArtifact"]] = relationship(
        back_populates="request", lazy="raise", cascade="save-update, merge"
    )
    requesting_principal: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    state: Mapped[ApprovalState] = mapped_column(enum_type(ApprovalState), server_default=text("'pending'"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRequestArtifact(IdentityMixin, Base):
    __tablename__ = "approval_request_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(["artifact_id", "checksum_digest"],
                             ["artifact_metadata.id", "artifact_metadata.checksum_digest"], ondelete="RESTRICT"),
        UniqueConstraint("request_id", "artifact_id"),
    )

    request_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="RESTRICT"), index=True
    )
    artifact_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), index=True)
    checksum_digest: Mapped[str] = mapped_column(String(64))
    request: Mapped[ApprovalRequest] = relationship(back_populates="artifact_digests")


class ApprovalDecisionRecord(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        ForeignKeyConstraint(["request_id", "request_revision"],
                             ["approval_requests.id", "approval_requests.revision"], ondelete="RESTRICT"),
        UniqueConstraint("request_id", "request_revision"),
        Index("ix_approval_decisions_request_id", "request_id"),
    )

    request_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    request_revision: Mapped[int]
    authenticated_user: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[ApprovalDecision] = mapped_column(enum_type(ApprovalDecision))
    reason: Mapped[str] = mapped_column(String(4096))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)


class EffectIntent(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "effect_intents"
    __table_args__ = (
        ForeignKeyConstraint(["attempt_id", "task_id"], ["task_attempts.id", "task_attempts.task_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["approval_request_id", "task_id", "attempt_id", "action_digest"],
                             ["approval_requests.id", "approval_requests.task_id", "approval_requests.attempt_id",
                              "approval_requests.action_digest"], ondelete="RESTRICT"),
        Index("ix_effect_intents_attempt_id", "attempt_id"),
        Index("ix_effect_intents_task_id", "task_id"),
        Index("ix_effect_intents_approval_request_id", "approval_request_id"),
    )

    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT")
    )
    attempt_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    action_digest: Mapped[str] = mapped_column(String(64))
    approval_request_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    business_idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    execution_status: Mapped[EffectStatus] = mapped_column(enum_type(EffectStatus), server_default=text("'prepared'"))
    external_reference: Mapped[str | None] = mapped_column(String(2048))