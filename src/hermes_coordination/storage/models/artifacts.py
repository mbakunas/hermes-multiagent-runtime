from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID as UUIDValue

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdentityMixin, TimestampMixin, bounded_json, enum_type
from .enums import ArtifactState

if TYPE_CHECKING:
    from .attempts import TaskAttempt
    from .identities import Principal
    from .tasks import Task


class ArtifactMetadata(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "artifact_metadata"
    __table_args__ = (
        ForeignKeyConstraint(["producing_attempt_id", "producing_task_id"],
                             ["task_attempts.id", "task_attempts.task_id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "checksum_digest"),
        UniqueConstraint("provider", "export_identifier", "environment", "storage_key"),
        CheckConstraint("byte_size >= 0", name="byte_size"),
        CheckConstraint("checksum_algorithm = 'sha256' AND checksum_digest ~ '^[0-9a-f]{64}$'", name="checksum"),
        CheckConstraint(
            "(producing_task_id IS NOT NULL AND producing_attempt_id IS NOT NULL) OR "
            "(producing_task_id IS NULL AND producing_attempt_id IS NULL AND "
            "COALESCE(provenance->>'origin', '') = 'external_intake')",
            name="producing_origin",
        ),
        CheckConstraint("provider = 'nas'", name="phase_one_provider"),
        CheckConstraint("storage_key !~ '(^/|(^|/)\\.\\.(/|$))'", name="relative_key"),
        bounded_json("provenance"), bounded_json("retention_policy"),
        Index("ix_artifact_metadata_producing_task_id", "producing_task_id"),
        Index("ix_artifact_metadata_producing_attempt_id", "producing_attempt_id"),
        Index("ix_artifact_metadata_retention", "state", "retention_expires_at"),
    )

    producing_task_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT")
    )
    producing_attempt_id: Mapped[UUIDValue | None] = mapped_column(UUID(as_uuid=True))
    uploader_identity: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    artifact_kind: Mapped[str] = mapped_column(String(128))
    media_type: Mapped[str] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    checksum_algorithm: Mapped[str] = mapped_column(String(16), server_default=text("'sha256'"))
    checksum_digest: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32), server_default=text("'nas'"))
    export_identifier: Mapped[str] = mapped_column(String(128))
    environment: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(255))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[ArtifactState] = mapped_column(enum_type(ArtifactState), server_default=text("'staged'"))
    retention_policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_hold: Mapped[bool] = mapped_column(server_default=text("false"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_metadata.id", ondelete="RESTRICT"), index=True
    )

    producing_task: Mapped["Task | None"] = relationship(viewonly=True, lazy="raise")
    producing_attempt: Mapped["TaskAttempt | None"] = relationship(viewonly=True, lazy="raise")
    uploader: Mapped["Principal"] = relationship(viewonly=True, lazy="raise")
    supersedes: Mapped["ArtifactMetadata | None"] = relationship(
        remote_side="ArtifactMetadata.id", viewonly=True, lazy="raise"
    )
    lineage: Mapped[list["ArtifactLineage"]] = relationship(
        foreign_keys="ArtifactLineage.artifact_id", viewonly=True, lazy="raise"
    )


class ArtifactLineage(IdentityMixin, Base):
    __tablename__ = "artifact_lineage"
    __table_args__ = (
        UniqueConstraint("artifact_id", "source_artifact_id"),
        CheckConstraint("artifact_id <> source_artifact_id", name="not_self"),
    )

    artifact_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_metadata.id", ondelete="RESTRICT"), index=True
    )
    source_artifact_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact_metadata.id", ondelete="RESTRICT"), index=True
    )

    artifact: Mapped["ArtifactMetadata"] = relationship(
        foreign_keys=[artifact_id], viewonly=True, lazy="raise"
    )
    source: Mapped["ArtifactMetadata"] = relationship(
        foreign_keys=[source_artifact_id], viewonly=True, lazy="raise"
    )