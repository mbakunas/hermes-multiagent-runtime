from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID as UUIDValue

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, TimestampMixin, VersionMixin, bounded_json, enum_type
from .enums import DependencyCondition, RequirementKind, TaskState

if TYPE_CHECKING:
    from .assignments import Assignment
    from .attempts import TaskAttempt
    from .capabilities import CapabilityDefinition
    from .workflows import Workflow


class Task(IdentityMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("id", "workflow_id"),
        ForeignKeyConstraint(
            ["current_attempt_id", "id"], ["task_attempts.id", "task_attempts.task_id"],
            name="fk_tasks_current_attempt", use_alter=True,
            deferrable=True, initially="DEFERRED", ondelete="RESTRICT",
        ),
        bounded_json("output_contract"),
        CheckConstraint("version > 0", name="version"),
        CheckConstraint("deadline_at IS NULL OR deadline_at >= available_at", name="deadline"),
        Index("ix_tasks_schedule", "state", "available_at", "deadline_at"),
        Index("ix_tasks_deadline_at", "deadline_at"),
        Index("ix_tasks_current_attempt_id", "current_attempt_id"),
    )

    workflow_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="RESTRICT"), index=True
    )
    specification_revision: Mapped[str] = mapped_column(String(128))
    input_references: Mapped[list["TaskInputReference"]] = relationship(
        back_populates="task", lazy="raise", cascade="save-update, merge"
    )
    output_contract: Mapped[dict[str, Any]] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[TaskState] = mapped_column(enum_type(TaskState), server_default=text("'pending'"))
    cancellation_intent: Mapped[bool] = mapped_column(server_default=text("false"))
    current_attempt_id: Mapped[UUIDValue | None] = mapped_column(UUID(as_uuid=True))

    workflow: Mapped["Workflow"] = relationship(viewonly=True, lazy="raise")
    capabilities: Mapped[list["TaskCapability"]] = relationship(viewonly=True, lazy="raise")
    assignments: Mapped[list["Assignment"]] = relationship(viewonly=True, lazy="raise")
    attempts: Mapped[list["TaskAttempt"]] = relationship(
        foreign_keys="TaskAttempt.task_id", viewonly=True, lazy="raise"
    )
    current_attempt: Mapped["TaskAttempt | None"] = relationship(
        primaryjoin="and_(Task.current_attempt_id == TaskAttempt.id, Task.id == TaskAttempt.task_id)",
        foreign_keys=[current_attempt_id], viewonly=True, lazy="raise",
    )
    prerequisites: Mapped[list["TaskDependency"]] = relationship(
        foreign_keys="TaskDependency.dependent_task_id", viewonly=True, lazy="raise"
    )
    dependents: Mapped[list["TaskDependency"]] = relationship(
        foreign_keys="TaskDependency.prerequisite_task_id", viewonly=True, lazy="raise"
    )


class TaskInputReference(IdentityMixin, Base):
    __tablename__ = "task_input_references"
    __table_args__ = (
        CheckConstraint(
            "(artifact_id IS NOT NULL)::integer + (external_reference IS NOT NULL)::integer = 1",
            name="one_source",
        ),
        UniqueConstraint("task_id", "artifact_id"),
        UniqueConstraint("task_id", "external_reference"),
    )

    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )
    artifact_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_metadata.id", ondelete="RESTRICT"), index=True
    )
    external_reference: Mapped[str | None] = mapped_column(String(2048))
    task: Mapped[Task] = relationship(back_populates="input_references")


class TaskCapability(IdentityMixin, Base):
    __tablename__ = "task_capabilities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "capability_name"],
            ["capability_definitions.id", "capability_definitions.canonical_name"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "capability_name"),
    )

    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )
    capability_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True), index=True)
    capability_name: Mapped[str] = mapped_column(String(128))
    version_constraint: Mapped[str] = mapped_column(String(128))
    requirement_kind: Mapped[RequirementKind] = mapped_column(enum_type(RequirementKind))

    task: Mapped["Task"] = relationship(viewonly=True, lazy="raise")
    capability: Mapped["CapabilityDefinition"] = relationship(viewonly=True, lazy="raise")


class TaskDependency(IdentityMixin, Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("prerequisite_task_id", "dependent_task_id"),
        CheckConstraint("prerequisite_task_id <> dependent_task_id", name="not_self"),
    )

    prerequisite_task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )
    dependent_task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )
    condition: Mapped[DependencyCondition] = mapped_column(
        enum_type(DependencyCondition), server_default=text("'success'")
    )

    prerequisite: Mapped["Task"] = relationship(
        foreign_keys=[prerequisite_task_id], viewonly=True, lazy="raise"
    )
    dependent: Mapped["Task"] = relationship(
        foreign_keys=[dependent_task_id], viewonly=True, lazy="raise"
    )