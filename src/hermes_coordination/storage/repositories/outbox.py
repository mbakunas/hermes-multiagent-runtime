from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, literal, or_, select, text, tuple_, update
from sqlalchemy.orm import Session

from ..models import OutboxEvent
from ..models.enums import OutboxPublicationStatus
from .base import Repository


class OutboxRepository(Repository[OutboxEvent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OutboxEvent)

    def lease(self, *, limit: int = 100, lease_seconds: int = 30) -> Sequence[OutboxEvent]:
        if not 1 <= limit <= 1000 or not 1 <= lease_seconds <= 3600:
            raise ValueError("Invalid outbox batch or lease size")
        candidates = select(OutboxEvent.id, OutboxEvent.created_at).where(or_(
            (OutboxEvent.publication_status == OutboxPublicationStatus.PENDING)
            & (OutboxEvent.retry_at <= func.current_timestamp()),
            (OutboxEvent.publication_status == OutboxPublicationStatus.PUBLISHING)
            & (OutboxEvent.publisher_lease_expires_at <= func.clock_timestamp()),
        )).order_by(OutboxEvent.retry_at, OutboxEvent.created_at).limit(limit).with_for_update(skip_locked=True).cte("candidates")
        statement = update(OutboxEvent).where(
            tuple_(OutboxEvent.id, OutboxEvent.created_at).in_(select(candidates.c.id, candidates.c.created_at))
        ).values(
            publication_status=OutboxPublicationStatus.PUBLISHING,
            publisher_lease=func.gen_random_uuid(),
            publisher_lease_expires_at=func.current_timestamp() + literal(lease_seconds) * text("INTERVAL '1 second'"),
        ).returning(OutboxEvent)
        return self.session.scalars(statement.execution_options(populate_existing=True)).all()

    def mark_published(self, event_id: UUID, token: UUID) -> bool:
        statement = self._owned_update(event_id, token).values(
            publication_status=OutboxPublicationStatus.PUBLISHED,
            publisher_lease=None, publisher_lease_expires_at=None,
        ).returning(OutboxEvent.id)
        return self.session.scalar(statement) is not None

    def retry(self, event_id: UUID, token: UUID, delay_seconds: int) -> bool:
        if not 0 <= delay_seconds <= 604800:
            raise ValueError("Invalid outbox retry delay")
        statement = self._owned_update(event_id, token).values(
            publication_status=OutboxPublicationStatus.PENDING,
            publisher_lease=None, publisher_lease_expires_at=None,
            retry_at=func.current_timestamp() + literal(delay_seconds) * text("INTERVAL '1 second'"),
        ).returning(OutboxEvent.id)
        return self.session.scalar(statement) is not None

    def _owned_update(self, event_id: UUID, token: UUID):
        return update(OutboxEvent).where(
            OutboxEvent.id == event_id, OutboxEvent.publisher_lease == token,
            OutboxEvent.publication_status == OutboxPublicationStatus.PUBLISHING,
            OutboxEvent.publisher_lease_expires_at > func.clock_timestamp(),
        )