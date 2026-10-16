from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, TimestampMixin, VersionMixin, bounded_json, enum_type
from .enums import WorkflowStatus

if TYPE_CHECKING:
    from .requests import Request
    from .tasks import Task


class Workflow(IdentityMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (bounded_json("budget_json"), CheckConstraint("version > 0", name="version"))

    request_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requests.id", ondelete="RESTRICT"), index=True
    )
    policy_revision: Mapped[str] = mapped_column(String(128))
    status: Mapped[WorkflowStatus] = mapped_column(
        enum_type(WorkflowStatus), server_default=text("'pending'")
    )
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    request: Mapped["Request"] = relationship(viewonly=True, lazy="raise")
    tasks: Mapped[list["Task"]] = relationship(viewonly=True, lazy="raise")