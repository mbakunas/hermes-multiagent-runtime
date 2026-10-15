from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ProposalDecisionRecord, ProposalTask, WorkProposal
from ..models.enums import ProposalState
from .base import Repository
from .ownership import write_owned_record


class ProposalRepository(Repository[WorkProposal]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, WorkProposal)

    def by_submission_key(self, key: str) -> WorkProposal | None:
        return self.session.scalar(select(WorkProposal).where(WorkProposal.submission_idempotency_key == key))

    def submitted(self, *, limit: int = 100, lock: bool = False) -> Sequence[WorkProposal]:
        statement = select(WorkProposal).where(WorkProposal.state == ProposalState.SUBMITTED)
        statement = statement.order_by(WorkProposal.created_at, WorkProposal.id).limit(limit)
        if lock:
            statement = statement.with_for_update(skip_locked=True)
        return self.session.scalars(statement).all()

    def submit_owned(self, attempt_id: UUID, token: UUID, data: dict[str, Any]) -> UUID:
        return write_owned_record(self.session, "work_proposals", attempt_id, token, data)

    def add_decision(self, decision: ProposalDecisionRecord) -> ProposalDecisionRecord:
        self.session.add(decision)
        self.session.flush()
        return decision

    def add_task_link(self, link: ProposalTask) -> ProposalTask:
        self.session.add(link)
        self.session.flush()
        return link