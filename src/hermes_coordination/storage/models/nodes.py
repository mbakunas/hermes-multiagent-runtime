from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, TimestampMixin, bounded_json, enum_type
from .enums import NodeState

if TYPE_CHECKING:
    from .agents import AgentInstance, AgentPlacement


class Node(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "nodes"
    __table_args__ = (bounded_json("metadata_json"),)

    name: Mapped[str] = mapped_column(String(128), unique=True)
    enrollment_identity: Mapped[str] = mapped_column(String(255), unique=True)
    desired_state: Mapped[NodeState] = mapped_column(
        enum_type(NodeState), server_default=text("'enrolled'")
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    instances: Mapped[list["AgentInstance"]] = relationship(viewonly=True, lazy="raise")
    placements: Mapped[list["AgentPlacement"]] = relationship(viewonly=True, lazy="raise")