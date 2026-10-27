"""Read-only access to the transactions database.

Two layers keep the service read-only:
1. It connects as `app_readonly`, which has SELECT grants only. `ensure_cannot_write()` runs
   at startup and refuses to continue if the account could change any table.
2. Every session runs inside `SET TRANSACTION READ ONLY`, so writes fail even if the
   service were misconfigured with a writable account.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TABLES_WITH_WRITE_ACCESS = text(
    """
    SELECT tables.relname
    FROM pg_catalog.pg_class AS tables
    JOIN pg_catalog.pg_namespace AS schemas ON schemas.oid = tables.relnamespace
    WHERE schemas.nspname = 'public'
      AND tables.relkind IN ('r', 'p')
      AND has_table_privilege(current_user, tables.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')
    ORDER BY tables.relname
    """
)


class WritableDatabaseAccountError(RuntimeError):
    pass


class ReadOnlyDatabase:
    def __init__(self, database_url: str, pool_size: int = 5, max_overflow: int = 5) -> None:
        self._engine = create_async_engine(
            database_url, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session inside a read-only transaction, rolled back when the block ends."""
        async with self._session_factory() as session:
            # The first statement starts the transaction, so this applies to all that follow.
            await session.execute(text("SET TRANSACTION READ ONLY"))
            try:
                yield session
            finally:
                await session.rollback()

    async def ensure_cannot_write(self) -> None:
        """Startup check: fail if the configured account can write to any table."""
        async with self.session() as session:
            writable_tables = list((await session.execute(TABLES_WITH_WRITE_ACCESS)).scalars())
        if writable_tables:
            raise WritableDatabaseAccountError(
                "the Transaction Service must connect with a read-only account; this account "
                f"can write to: {', '.join(writable_tables)}"
            )

    async def dispose(self) -> None:
        await self._engine.dispose()
