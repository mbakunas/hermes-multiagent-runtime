from uuid import UUID as UUIDValue

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdentityMixin, TimestampMixin, enum_type
from .enums import RequestStatus


class Request(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "requests"

    authenticated_initiator: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    concierge_reference: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    intent_reference: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_metadata.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[RequestStatus] = mapped_column(
        enum_type(RequestStatus), server_default=text("'submitted'")
    )