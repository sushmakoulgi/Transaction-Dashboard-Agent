"""Transaction Service app factory.

Run with: uvicorn transaction_service.main:create_app --factory --port 8200
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from shared.clock import Clock, SystemClock
from shared.contracts import HealthStatus
from shared.http.bearer_auth import bearer_token_dependencies
from transaction_service.api.dependencies import ServiceContainer, get_services
from transaction_service.api.error_handlers import register_error_handlers
from transaction_service.api.routes import router
from transaction_service.filtering.business_calendar import BusinessCalendar
from transaction_service.filtering.condition_resolver import ConditionResolver
from transaction_service.filtering.field_types import FieldValueCoercer
from transaction_service.filtering.filter_compiler import FilterCompiler
from transaction_service.filtering.filter_validator import FilterValidator
from transaction_service.persistence.database import ReadOnlyDatabase
from transaction_service.services.search_service import SearchService
from transaction_service.services.transaction_detail_service import TransactionDetailService
from transaction_service.settings import TransactionServiceSettings


def build_services(
    database: ReadOnlyDatabase, settings: TransactionServiceSettings, clock: Clock
) -> ServiceContainer:
    business_timezone = ZoneInfo(settings.business_timezone)
    calendar = BusinessCalendar(business_timezone, clock)
    validator = FilterValidator(ConditionResolver(FieldValueCoercer(business_timezone), calendar))
    return ServiceContainer(
        database=database,
        search_service=SearchService(database, validator, calendar, FilterCompiler()),
        transaction_detail_service=TransactionDetailService(database, calendar),
    )


def create_app(
    settings: TransactionServiceSettings | None = None, clock: Clock | None = None
) -> FastAPI:
    service_settings = settings or TransactionServiceSettings()  # values from the environment
    service_clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = ReadOnlyDatabase(
            str(service_settings.postgres_dsn_readonly),
            pool_size=service_settings.database_pool_size,
            max_overflow=service_settings.database_max_overflow,
        )
        try:
            await database.ensure_cannot_write()  # refuse to start with a writable account
            app.state.services = build_services(database, service_settings, service_clock)
            yield
        finally:
            await database.dispose()

    app = FastAPI(
        title="Transaction Service",
        description="Read-only search over transactions and chargebacks with safe filters.",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(
        router,
        dependencies=bearer_token_dependencies(
            service_settings.auth_enabled,
            service_settings.transaction_service_api_token,
            "Transaction Service",
        ),
    )

    @app.get("/health", response_model=HealthStatus, tags=["health"])
    async def health(request: Request) -> JSONResponse:
        """No token needed. 503 when the database can't be reached."""
        try:
            async with get_services(request).database.session() as session:
                await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ok"})

    return app
