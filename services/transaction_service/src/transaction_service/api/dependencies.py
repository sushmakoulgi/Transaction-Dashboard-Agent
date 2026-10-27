"""The services one app instance uses, built at startup and read by the routes."""

from dataclasses import dataclass

from fastapi import Request

from transaction_service.persistence.database import ReadOnlyDatabase
from transaction_service.services.search_service import SearchService
from transaction_service.services.transaction_detail_service import TransactionDetailService


@dataclass(frozen=True)
class ServiceContainer:
    database: ReadOnlyDatabase
    search_service: SearchService
    transaction_detail_service: TransactionDetailService


def get_services(request: Request) -> ServiceContainer:
    services: ServiceContainer = request.app.state.services
    return services


def get_search_service(request: Request) -> SearchService:
    return get_services(request).search_service


def get_transaction_detail_service(request: Request) -> TransactionDetailService:
    return get_services(request).transaction_detail_service
