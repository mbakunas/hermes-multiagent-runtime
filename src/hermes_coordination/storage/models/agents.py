from datetime import datetime
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdentityMixin, TimestampMixin, enum_type
from .enums import AgentRole, AgentState, InstanceState, ReadinessState


class Agent(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[AgentRole] = mapped_column(enum_type(AgentRole))
    desired_state: Mapped[AgentState] = mapped_column(
        enum_type(AgentState), server_default=text("'provisioned'")
    )
    approved_profile_revision: Mapped[str] = mapped_column(String(128))


class AgentInstance(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "agent_instances"
    __table_args__ = (
        UniqueConstraint("id", "agent_id"),
        CheckConstraint("capacity = 1", name="phase_one_capacity"),
        Index(
            "uq_agent_instances_active_incarnation", "agent_id", unique=True,
            postgresql_where=text("state IN ('registering', 'ready', 'draining', 'suspect')"),
        ),
        Index("ix_agent_instances_eligibility", "state", "readiness", "last_heartbeat_at"),
    )

    agent_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    node_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="RESTRICT"), index=True
    )
    hermes_version: Mapped[str] = mapped_column(String(128))
    verified_profile_digest: Mapped[str] = mapped_column(String(128))
    state: Mapped[InstanceState] = mapped_column(
        enum_type(InstanceState), server_default=text("'registering'")
    )
    readiness: Mapped[ReadinessState] = mapped_column(
        enum_type(ReadinessState), server_default=text("'not_ready'")
    )
    capacity: Mapped[int] = mapped_column(server_default=text("1"))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))