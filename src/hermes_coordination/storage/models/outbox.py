from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, EventIdentityMixin, bounded_json, enum_type
from .enums import OutboxPublicationStatus

if TYPE_CHECKING:
    from .identities import CoordinationEntity


class OutboxEvent(EventIdentityMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        ForeignKeyConstraint(["entity_id", "entity_type"],
                             ["coordination_entities.id", "coordination_entities.entity_type"], ondelete="RESTRICT"),
        CheckConstraint("entity_version > 0", name="entity_version"),
        CheckConstraint(
            "(publication_status = 'publishing' AND publisher_lease IS NOT NULL AND publisher_lease_expires_at IS NOT NULL) OR "
            "(publication_status <> 'publishing' AND publisher_lease IS NULL AND publisher_lease_expires_at IS NULL)",
            name="publisher_ownership",
        ),
        bounded_json("envelope", limit=8192),
        Index("ix_outbox_events_publication_status", "publication_status"),
        Index("ix_outbox_events_retry_at", "retry_at"),
        Index("ix_outbox_events_entity_id", "entity_id"),
        Index("ix_outbox_events_pending", "retry_at", "created_at",
              postgresql_where=text("publication_status <> 'published'")),
        Index("ix_outbox_events_publisher_lease_expires_at", "publisher_lease_expires_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    entity_id: Mapped[UUIDValue] = mapped_column(UUID(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(String(63))
    entity_version: Mapped[int]
    event_type: Mapped[str] = mapped_column(String(128))
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    topic: Mapped[str] = mapped_column(String(512))
    publication_status: Mapped[OutboxPublicationStatus] = mapped_column(
        enum_type(OutboxPublicationStatus), server_default=text("'pending'")
    )
    publisher_lease: Mapped[UUIDValue | None] = mapped_column(UUID(as_uuid=True))
    publisher_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))

    entity: Mapped["CoordinationEntity"] = relationship(viewonly=True, lazy="raise")