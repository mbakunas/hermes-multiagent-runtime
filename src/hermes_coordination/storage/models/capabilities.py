from datetime import datetime
from typing import Any
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedMixin, IdentityMixin, bounded_json


class CapabilityDefinition(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "capability_definitions"
    __table_args__ = (
        UniqueConstraint("canonical_name", "contract_version"),
        UniqueConstraint("id", "canonical_name"),
        CheckConstraint("canonical_name ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'", name="canonical_name"),
        CheckConstraint("contract_version > 0", name="positive_version"),
        bounded_json("approval_requirements"),
    )

    canonical_name: Mapped[str] = mapped_column(String(128))
    contract_version: Mapped[int] = mapped_column(server_default=text("1"))
    description: Mapped[str] = mapped_column(String(4096))
    approval_requirements: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )


class InstanceCapability(IdentityMixin, Base):
    __tablename__ = "instance_capabilities"
    __table_args__ = (UniqueConstraint("instance_id", "capability_id"),)

    instance_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_instances.id", ondelete="RESTRICT"), index=True
    )
    capability_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capability_definitions.id", ondelete="RESTRICT"), index=True
    )
    verified_source_revision: Mapped[str] = mapped_column(String(128))
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )