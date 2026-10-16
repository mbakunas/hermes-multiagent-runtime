from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, TimestampMixin, enum_type
from .enums import AgentRole, AgentState, InstanceState, ReadinessState

if TYPE_CHECKING:
    from .capabilities import InstanceCapability
    from .nodes import Node


class Agent(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[AgentRole] = mapped_column(enum_type(AgentRole))
    desired_state: Mapped[AgentState] = mapped_column(
        enum_type(AgentState), server_default=text("'provisioned'")
    )
    approved_profile_revision: Mapped[str] = mapped_column(String(128))

    instances: Mapped[list["AgentInstance"]] = relationship(
        back_populates="agent", viewonly=True, lazy="raise"
    )
    placements: Mapped[list["AgentPlacement"]] = relationship(viewonly=True, lazy="raise")


class AgentPlacement(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "agent_placements"
    __table_args__ = (UniqueConstraint("agent_id", "node_id"),)

    agent_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    node_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="RESTRICT"), index=True
    )
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))

    agent: Mapped["Agent"] = relationship(viewonly=True, lazy="raise")
    node: Mapped["Node"] = relationship(viewonly=True, lazy="raise")


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

    agent: Mapped["Agent"] = relationship(back_populates="instances", viewonly=True, lazy="raise")
    node: Mapped["Node"] = relationship(viewonly=True, lazy="raise")
    capabilities: Mapped[list["InstanceCapability"]] = relationship(viewonly=True, lazy="raise")