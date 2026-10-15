from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from ..models import ArtifactMetadata
from .base import Repository
from .ownership import write_owned_record


class ArtifactRepository(Repository[ArtifactMetadata]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ArtifactMetadata)

    def register_verified_owned(self, attempt_id: UUID, token: UUID, metadata: dict[str, Any]) -> UUID:
        return write_owned_record(self.session, "artifact_metadata", attempt_id, token, metadata)

    def accept_owned(self, artifact_id: UUID, attempt_id: UUID, token: UUID) -> bool:
        statement = text("SELECT public.hermes_accept_artifact(:artifact, :attempt, :token)").bindparams(
            bindparam("artifact", type_=PGUUID(as_uuid=True)), bindparam("attempt", type_=PGUUID(as_uuid=True)),
            bindparam("token", type_=PGUUID(as_uuid=True)),
        )
        return bool(self.session.scalar(statement, {"artifact": artifact_id, "attempt": attempt_id, "token": token}))