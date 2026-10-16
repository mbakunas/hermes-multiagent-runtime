from typing import TYPE_CHECKING, Any
from uuid import UUID as UUIDValue

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, TimestampMixin, bounded_json, enum_type
from .enums import AssignmentState

if TYPE_CHECKING:
    from .agents import AgentInstance
    from .attempts import TaskAttempt
    from .identities import Principal
    from .tasks import Task


class Assignment(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("id", "task_id"),
        bounded_json("capability_snapshot", "array"),
        Index("uq_assignments_active_task", "task_id", unique=True,
              postgresql_where=text("state = 'active'")),
        Index("uq_assignments_active_instance", "instance_id", unique=True,
              postgresql_where=text("state = 'active'")),
    )

    task_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )
    instance_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_instances.id", ondelete="RESTRICT"), index=True
    )
    authoritative_assigner: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    selection_rationale: Mapped[str] = mapped_column(String(4096))
    capability_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    state: Mapped[AssignmentState] = mapped_column(
        enum_type(AssignmentState), server_default=text("'active'")
    )

    task: Mapped["Task"] = relationship(viewonly=True, lazy="raise")
    instance: Mapped["AgentInstance"] = relationship(viewonly=True, lazy="raise")
    assigner: Mapped["Principal"] = relationship(viewonly=True, lazy="raise")
    attempt: Mapped["TaskAttempt | None"] = relationship(viewonly=True, lazy="raise")