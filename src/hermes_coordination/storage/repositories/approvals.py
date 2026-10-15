from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from ..models import ApprovalDecisionRecord, ApprovalRequest, EffectIntent
from ..models.enums import ApprovalState, EffectStatus
from .base import Repository
from .ownership import write_owned_record


class ApprovalRepository(Repository[ApprovalRequest]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ApprovalRequest)

    def pending(self, *, limit: int = 100) -> Sequence[ApprovalRequest]:
        return self.session.scalars(select(ApprovalRequest).where(ApprovalRequest.state == ApprovalState.PENDING)
                                    .order_by(ApprovalRequest.expires_at).limit(limit)).all()

    def request_owned(self, attempt_id: UUID, token: UUID, data: dict[str, Any]) -> UUID:
        return write_owned_record(self.session, "approval_requests", attempt_id, token, data)

    def add_decision(self, decision: ApprovalDecisionRecord) -> ApprovalDecisionRecord:
        self.session.add(decision)
        self.session.flush()
        return decision

    def record_effect_owned(self, attempt_id: UUID, token: UUID, data: dict[str, Any]) -> UUID:
        return write_owned_record(self.session, "effect_intents", attempt_id, token, data)

    def set_effect_status_owned(self, effect_id: UUID, attempt_id: UUID, token: UUID,
                                status: EffectStatus, external_reference: str | None = None) -> bool:
        statement = text('SELECT public.hermes_set_effect_status(:effect, :attempt, :token, '
                         'CAST(:status AS public."EffectStatus"), :reference)').bindparams(
            bindparam("effect", type_=PGUUID(as_uuid=True)), bindparam("attempt", type_=PGUUID(as_uuid=True)),
            bindparam("token", type_=PGUUID(as_uuid=True)),
        )
        return bool(self.session.scalar(statement, {"effect": effect_id, "attempt": attempt_id,
                    "token": token, "status": status.value, "reference": external_reference}))

    def effects_for_attempt(self, attempt_id: UUID) -> Sequence[EffectIntent]:
        return self.session.scalars(select(EffectIntent).where(EffectIntent.attempt_id == attempt_id)).all()