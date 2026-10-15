from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from ..models import Agent, AgentInstance, CapabilityDefinition, InstanceCapability, Node, Principal
from ..models.enums import InstanceState, ReadinessState
from .base import Repository


class NodeRepository(Repository[Node]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Node)

    def by_name(self, name: str) -> Node | None:
        return self.session.scalar(select(Node).where(Node.name == name))

    def heartbeat(self, instance_id: UUID | None = None) -> bool:
        statement = text("SELECT public.hermes_heartbeat(:instance)").bindparams(
            bindparam("instance", type_=PGUUID(as_uuid=True))
        )
        return bool(self.session.scalar(statement, {"instance": instance_id}))


class AgentRepository(Repository[Agent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Agent)

    def by_name(self, name: str) -> Agent | None:
        return self.session.scalar(select(Agent).where(Agent.name == name))


class InstanceRepository(Repository[AgentInstance]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, AgentInstance)

    def for_node(self, node_id: UUID) -> Sequence[AgentInstance]:
        return self.session.scalars(select(AgentInstance).where(AgentInstance.node_id == node_id)).all()

    def register(self, agent_id: UUID, hermes_version: str, profile_digest: str) -> UUID:
        statement = text("SELECT public.hermes_register_instance(:agent, :version, :digest)").bindparams(
            bindparam("agent", type_=PGUUID(as_uuid=True))
        )
        return self.session.scalar(statement, {"agent": agent_id, "version": hermes_version, "digest": profile_digest})

    def set_state(self, instance_id: UUID, state: InstanceState, readiness: ReadinessState) -> bool:
        statement = text('SELECT public.hermes_set_instance_state(:instance, CAST(:state AS public."InstanceState"), '
                         'CAST(:readiness AS public."ReadinessState"))').bindparams(
            bindparam("instance", type_=PGUUID(as_uuid=True))
        )
        return bool(self.session.scalar(statement, {"instance": instance_id, "state": state.value, "readiness": readiness.value}))


class CapabilityRepository(Repository[CapabilityDefinition]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, CapabilityDefinition)

    def inventory(self, instance_id: UUID) -> Sequence[CapabilityDefinition]:
        return self.session.scalars(
            select(CapabilityDefinition).join(InstanceCapability, InstanceCapability.capability_id == CapabilityDefinition.id)
            .where(InstanceCapability.instance_id == instance_id)
        ).all()


class PrincipalRepository(Repository[Principal]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Principal)

    def by_subject(self, subject: str) -> Principal | None:
        return self.session.scalar(select(Principal).where(Principal.subject == subject))