from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Base


class ConcurrentWriteError(RuntimeError):
    pass


class Repository[ModelT: Base]:
    def __init__(self, session: Session, model: type[ModelT]) -> None:
        self.session = session
        self.model = model

    def get(self, identity: UUID, *, lock: bool = False) -> ModelT | None:
        statement = select(self.model).where(self.model.__table__.c.id == identity)
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement.execution_options(populate_existing=True))

    def add(self, record: ModelT) -> ModelT:
        self.session.add(record)
        self.session.flush()
        self.session.refresh(record)
        return record

    def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        return self.session.scalars(select(self.model).order_by(self.model.__table__.c.id).limit(limit).offset(offset)).all()

    def compare_and_set(self, identity: UUID, expected_version: int, values: dict[str, Any]) -> ModelT:
        if "id" in values or "version" in values:
            raise ValueError("Identity and version are not caller-editable")
        if "version" not in self.model.__table__.c:
            raise ValueError("This model has no optimistic version column")
        statement = (
            update(self.model)
            .where(self.model.__table__.c.id == identity, self.model.__table__.c.version == expected_version)
            .values(**values, version=expected_version + 1)
            .returning(self.model)
        )
        record = self.session.scalar(statement.execution_options(populate_existing=True))
        if record is None:
            raise ConcurrentWriteError("Missing record or stale version")
        return record