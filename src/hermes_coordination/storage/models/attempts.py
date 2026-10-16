from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, TimestampMixin, enum_type
from .enums import AttemptState

if TYPE_CHECKING:
    from .assignments import Assignment
    from .execution import ExecutionRun, TaskResult
    from .tasks import Task


class TaskAttempt(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        ForeignKeyConstraint(["assignment_id", "task_id"], ["assignments.id", "assignments.task_id"],
                             ondelete="RESTRICT"),
        UniqueConstraint("task_id", "attempt_number"),
        UniqueConstraint("id", "task_id"),
        UniqueConstraint("assignment_id"),
        CheckConstraint("attempt_number > 0", name="positive_attempt"),
        CheckConstraint("ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at", name="time_order"),
        Index("uq_task_attempts_active_task", "task_id", unique=True,
              postgresql_where=text("state IN ('assigned', 'running')")),
        Index("ix_task_attempts_leases", "lease_expires_at",
              postgresql_where=text("state IN ('assigned', 'running')")),
        Index("ix_task_attempts_task_id", "task_id"),
    )

    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT")
    )
    assignment_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    attempt_number: Mapped[int]
    state: Mapped[AttemptState] = mapped_column(enum_type(AttemptState), server_default=text("'assigned'"))
    ownership_token: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), server_default=text("gen_random_uuid()"), unique=True
    )
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_classification: Mapped[str | None] = mapped_column(String(128))

    task: Mapped["Task"] = relationship(foreign_keys=[task_id], viewonly=True, lazy="raise")
    assignment: Mapped["Assignment"] = relationship(viewonly=True, lazy="raise")
    execution_run: Mapped["ExecutionRun | None"] = relationship(viewonly=True, lazy="raise")
    result: Mapped["TaskResult | None"] = relationship(viewonly=True, lazy="raise")