# The agent service as a container image for Amazon ECR (ECS Express Mode / Fargate). Build
# from the repository root:
#
#   docker build --platform linux/arm64 --provenance=false -f agent_service.Dockerfile -t agent-service .
#
# Only the agent's own dependencies are installed (not the Transaction Service's). Settings come
# from environment variables (see .env.example); on ECS, ANTHROPIC_API_KEY is injected from AWS
# Secrets Manager. The image never contains .env (.dockerignore).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so source changes don't reinstall them. Only this service's workspace
# packages are copied: uv installs from the lockfile what they need, nothing of the others.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY services/agent_service/pyproject.toml services/agent_service/pyproject.toml
RUN uv sync --frozen --no-dev --package agent-service --no-install-workspace

COPY packages/shared packages/shared
COPY services/agent_service services/agent_service
RUN uv sync --frozen --no-dev --package agent-service

RUN useradd --create-home --uid 10001 app
USER app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Keep-alive (130 s) outlasts the load balancer's idle timeout (120 s), so the load balancer
# always closes idle connections first; otherwise a reused, just-closed one gives a random 502.
CMD ["uvicorn", "agent_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "130"]
