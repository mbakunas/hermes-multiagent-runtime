from datetime import datetime
from typing import Any
from uuid import UUID as UUIDValue

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, bounded_json, enum_type
from .enums import AcceptedOutcome, ExecutionStatus


class ExecutionRun(IdentityMixin, Base):
    __tablename__ = "execution_runs"
    __table_args__ = (bounded_json("usage_metadata"), UniqueConstraint("node_execution_id"))

    attempt_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="RESTRICT"), unique=True
    )
    node_execution_id: Mapped[str] = mapped_column(String(255))
    hermes_session_id: Mapped[str | None] = mapped_column(String(255))
    profile_identity: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    adapter_version: Mapped[str] = mapped_column(String(128))
    terminal_status: Mapped[ExecutionStatus | None] = mapped_column(enum_type(ExecutionStatus))
    usage_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskResult(IdentityMixin, Base):
    __tablename__ = "task_results"
    __table_args__ = (bounded_json("coordination_data"),)

    attempt_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="RESTRICT"), unique=True
    )
    accepted_outcome: Mapped[AcceptedOutcome] = mapped_column(enum_type(AcceptedOutcome))
    summary: Mapped[str] = mapped_column(String(4096))
    coordination_data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    artifact_references: Mapped[list["TaskResultArtifact"]] = relationship(
        back_populates="result", lazy="raise", cascade="save-update, merge"
    )
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))


class TaskResultArtifact(IdentityMixin, Base):
    __tablename__ = "task_result_artifacts"
    __table_args__ = (UniqueConstraint("result_id", "artifact_id"),)

    result_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_results.id", ondelete="RESTRICT"), index=True
    )
    artifact_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_metadata.id", ondelete="RESTRICT"), index=True
    )
    result: Mapped[TaskResult] = relationship(back_populates="artifact_references")