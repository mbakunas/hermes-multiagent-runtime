from typing import TYPE_CHECKING, Any
from uuid import UUID as UUIDValue

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, EventIdentityMixin, bounded_json, enum_type
from .enums import TaskState

if TYPE_CHECKING:
    from .identities import CoordinationEntity, Principal
    from .tasks import Task


class AuditEvent(EventIdentityMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["entity_id", "entity_type"],
                             ["coordination_entities.id", "coordination_entities.entity_type"], ondelete="RESTRICT"),
        bounded_json("payload"),
        Index("ix_audit_events_entity", "entity_id", "created_at"),
        Index("ix_audit_events_correlation_id", "correlation_id"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    entity_type: Mapped[str] = mapped_column(String(63))
    entity_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    actor: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coordination_entities.id", ondelete="RESTRICT")
    )
    causation_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coordination_entities.id", ondelete="RESTRICT"), index=True
    )

    entity: Mapped["CoordinationEntity"] = relationship(
        foreign_keys=[entity_id, entity_type], viewonly=True, lazy="raise"
    )
    actor_principal: Mapped["Principal"] = relationship(viewonly=True, lazy="raise")
    correlation: Mapped["CoordinationEntity"] = relationship(
        foreign_keys=[correlation_id], viewonly=True, lazy="raise"
    )
    causation: Mapped["CoordinationEntity | None"] = relationship(
        foreign_keys=[causation_id], viewonly=True, lazy="raise"
    )


class TaskEvent(EventIdentityMixin, Base):
    __tablename__ = "task_events"
    __table_args__ = {"postgresql_partition_by": "RANGE (created_at)"}

    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )
    actor: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(128))
    previous_state: Mapped[TaskState | None] = mapped_column(enum_type(TaskState))
    new_state: Mapped[TaskState] = mapped_column(enum_type(TaskState))
    reason: Mapped[str] = mapped_column(String(4096))
    correlation_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coordination_entities.id", ondelete="RESTRICT"), index=True
    )
    causation_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coordination_entities.id", ondelete="RESTRICT"), index=True
    )

    task: Mapped["Task"] = relationship(viewonly=True, lazy="raise")
    actor_principal: Mapped["Principal"] = relationship(viewonly=True, lazy="raise")
    correlation: Mapped["CoordinationEntity"] = relationship(
        foreign_keys=[correlation_id], viewonly=True, lazy="raise"
    )
    causation: Mapped["CoordinationEntity | None"] = relationship(
        foreign_keys=[causation_id], viewonly=True, lazy="raise"
    )