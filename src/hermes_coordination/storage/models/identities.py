from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedMixin, IdentityMixin, enum_type
from .enums import PrincipalKind


class CoordinationEntity(CreatedMixin, Base):
    __tablename__ = "coordination_entities"
    __table_args__ = (UniqueConstraint("id", "entity_type"), CheckConstraint("version > 0", name="version"))

    id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    entity_type: Mapped[str] = mapped_column(String(63))
    version: Mapped[int] = mapped_column(server_default=text("1"))


class Principal(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "principals"
    __table_args__ = (
        CheckConstraint("(kind = 'node') = (node_id IS NOT NULL)", name="node_binding"),
    )

    subject: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[PrincipalKind] = mapped_column(enum_type(PrincipalKind))
    database_role: Mapped[str | None] = mapped_column(String(63), unique=True)
    node_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="RESTRICT"), index=True
    )