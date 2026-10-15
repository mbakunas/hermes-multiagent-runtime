from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Session

from ..models import Assignment, TaskAttempt
from ..models.enums import AcceptedOutcome, AssignmentState, AttemptState
from .base import Repository


class AssignmentRepository(Repository[Assignment]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Assignment)

    def for_instance(self, instance_id: UUID, *, active_only: bool = True) -> Sequence[Assignment]:
        statement = select(Assignment).where(Assignment.instance_id == instance_id)
        if active_only:
            statement = statement.where(Assignment.state == AssignmentState.ACTIVE)
        return self.session.scalars(statement.order_by(Assignment.created_at, Assignment.id)).all()


class AttemptRepository(Repository[TaskAttempt]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, TaskAttempt)

    def for_task(self, task_id: UUID) -> Sequence[TaskAttempt]:
        return self.session.scalars(select(TaskAttempt).where(TaskAttempt.task_id == task_id)
                                    .order_by(TaskAttempt.attempt_number)).all()

    def expired(self, *, limit: int = 100) -> Sequence[TaskAttempt]:
        return self.session.scalars(select(TaskAttempt).where(
            TaskAttempt.state.in_((AttemptState.ASSIGNED, AttemptState.RUNNING)),
            TaskAttempt.lease_expires_at <= func.clock_timestamp(),
        ).order_by(TaskAttempt.lease_expires_at).limit(limit)).all()

    def claim(self, attempt_id: UUID, token: UUID, lease_seconds: int) -> bool:
        return self._lease("hermes_claim_attempt", attempt_id, token, lease_seconds)

    def renew(self, attempt_id: UUID, token: UUID, lease_seconds: int) -> bool:
        return self._lease("hermes_renew_attempt", attempt_id, token, lease_seconds)

    def _lease(self, function: str, attempt_id: UUID, token: UUID, seconds: int) -> bool:
        statement = text(f"SELECT public.{function}(:attempt, :token, :seconds)").bindparams(
            bindparam("attempt", type_=PGUUID(as_uuid=True)), bindparam("token", type_=PGUUID(as_uuid=True))
        )
        return bool(self.session.scalar(statement, {"attempt": attempt_id, "token": token, "seconds": seconds}))

    def complete(self, attempt_id: UUID, token: UUID, outcome: AcceptedOutcome, summary: str,
                 coordination_data: dict[str, Any], artifact_ids: Sequence[UUID] = (),
                 error_classification: str | None = None) -> UUID:
        statement = text('SELECT public.hermes_complete_attempt(:attempt, :token, '
                         'CAST(:outcome AS public."AcceptedOutcome"), :summary, :data, :artifacts, :error)').bindparams(
            bindparam("attempt", type_=PGUUID(as_uuid=True)), bindparam("token", type_=PGUUID(as_uuid=True)),
            bindparam("data", type_=JSONB), bindparam("artifacts", type_=ARRAY(PGUUID(as_uuid=True)))
        )
        return self.session.scalar(statement, {
            "attempt": attempt_id, "token": token, "outcome": outcome.value, "summary": summary,
            "data": coordination_data, "artifacts": list(artifact_ids), "error": error_classification,
        })


def write_owned_record(session: Session, table: str, attempt_id: UUID, token: UUID, data: dict[str, Any]) -> UUID:
    statement = text("SELECT public.hermes_write_owned_record(:table, :attempt, :token, :data)").bindparams(
        bindparam("attempt", type_=PGUUID(as_uuid=True)), bindparam("token", type_=PGUUID(as_uuid=True)),
        bindparam("data", type_=JSONB),
    )
    return session.scalar(statement, {"table": table, "attempt": attempt_id, "token": token, "data": data})