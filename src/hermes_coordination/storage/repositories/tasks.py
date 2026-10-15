from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Request, Task, TaskDependency, Workflow
from ..models.enums import TaskState
from .base import Repository


class RequestRepository(Repository[Request]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Request)

    def by_idempotency_key(self, key: str) -> Request | None:
        return self.session.scalar(select(Request).where(Request.idempotency_key == key))


class WorkflowRepository(Repository[Workflow]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Workflow)

    def for_request(self, request_id: UUID) -> Sequence[Workflow]:
        return self.session.scalars(select(Workflow).where(Workflow.request_id == request_id)).all()


class TaskRepository(Repository[Task]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Task)

    def ready(self, *, limit: int = 100, lock: bool = False) -> Sequence[Task]:
        statement = select(Task).where(
            Task.state == TaskState.READY, Task.available_at <= func.current_timestamp(),
            Task.cancellation_intent.is_(False),
        ).order_by(Task.priority.desc(), Task.available_at, Task.id).limit(limit)
        if lock:
            statement = statement.with_for_update(skip_locked=True)
        return self.session.scalars(statement).all()

    def for_workflow(self, workflow_id: UUID) -> Sequence[Task]:
        return self.session.scalars(select(Task).where(Task.workflow_id == workflow_id)).all()

    def dependencies(self, task_id: UUID) -> Sequence[TaskDependency]:
        return self.session.scalars(select(TaskDependency).where(TaskDependency.dependent_task_id == task_id)).all()