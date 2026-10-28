"""The objects one agent app instance uses, built at startup and read by the routes."""

from dataclasses import dataclass

from fastapi import Request

from agent_service.agent.query_agent import QueryAgent
from agent_service.memory.session_store import SessionStore
from agent_service.plan_execution.plan_executor import PlanExecutor
from shared.clients.transaction_service_client import TransactionServiceClient


@dataclass(frozen=True)
class AgentServiceContainer:
    query_agent: QueryAgent
    plan_executor: PlanExecutor
    sessions: SessionStore
    transaction_service: TransactionServiceClient


def get_container(request: Request) -> AgentServiceContainer:
    container: AgentServiceContainer = request.app.state.container
    return container
