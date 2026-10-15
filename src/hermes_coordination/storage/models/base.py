from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID as UUIDValue

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, MetaData, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        UUIDValue: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def enum_type(enum_class: type[StrEnum]) -> ENUM:
    return ENUM(
        enum_class,
        name=enum_class.__name__,
        values_callable=lambda values: [member.value for member in values],
        create_type=False,
        validate_strings=True,
    )


def bounded_json(column: str, shape: str = "object", limit: int = 65536) -> CheckConstraint:
    return CheckConstraint(
        f"jsonb_typeof({column}) = '{shape}' AND octet_length({column}::text) <= {limit}",
        name=f"{column}_bounded",
    )


class IdentityMixin:
    @declared_attr
    def id(cls) -> Mapped[UUIDValue]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("coordination_entities.id", ondelete="RESTRICT"),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        )


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )


class TimestampMixin(CreatedMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"), nullable=False,
    )


class VersionMixin:
    version: Mapped[int] = mapped_column(server_default=text("1"), nullable=False)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": cls.version}