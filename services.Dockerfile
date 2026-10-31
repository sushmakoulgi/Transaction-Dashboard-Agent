# One image for every Python container (db-setup, transaction-service,
# agent-service): the whole uv workspace, installed from the lockfile. Each compose service
# picks its own command.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so source changes don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/transaction_database/pyproject.toml packages/transaction_database/pyproject.toml
COPY services/transaction_service/pyproject.toml services/transaction_service/pyproject.toml
COPY services/agent_service/pyproject.toml services/agent_service/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace

COPY packages packages
COPY services services
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app
USER app
ENV PATH="/app/.venv/bin:$PATH"
