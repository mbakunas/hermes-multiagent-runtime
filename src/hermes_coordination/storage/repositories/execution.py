from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Session

from ..models import ExecutionRun
from ..models.enums import ExecutionStatus
from .base import Repository
from .ownership import write_owned_record


class ExecutionRepository(Repository[ExecutionRun]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ExecutionRun)

    def start_owned(self, attempt_id: UUID, token: UUID, node_execution_id: str, adapter_version: str) -> UUID:
        return write_owned_record(self.session, "execution_runs", attempt_id, token,
                                  {"node_execution_id": node_execution_id, "adapter_version": adapter_version})

    def finish_owned(self, attempt_id: UUID, token: UUID, session_id: str | None,
                     status: ExecutionStatus, usage: dict[str, Any]) -> bool:
        statement = text('SELECT public.hermes_finish_execution(:attempt, :token, :session, '
                         'CAST(:status AS public."ExecutionStatus"), :usage)').bindparams(
            bindparam("attempt", type_=PGUUID(as_uuid=True)), bindparam("token", type_=PGUUID(as_uuid=True)),
            bindparam("usage", type_=JSONB),
        )
        return bool(self.session.scalar(statement, {"attempt": attempt_id, "token": token,
                    "session": session_id, "status": status.value, "usage": usage}))