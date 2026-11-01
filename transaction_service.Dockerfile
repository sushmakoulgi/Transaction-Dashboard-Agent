# The Transaction Service as an AWS Lambda container image (Lambda Web Adapter + uvicorn).
#
#   docker build --platform linux/arm64 -f transaction_service.lambda.Dockerfile -t transaction-service-lambda .
#
# The adapter runs as a Lambda extension: it starts with the function, waits until /health
# answers, then turns each Function URL / API Gateway event into a plain HTTP request to
# uvicorn, so every FastAPI route works unchanged. Only the Transaction Service's own
# dependencies are installed (not the agent's), which keeps cold starts short.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so source changes don't reinstall them. uv needs every workspace
# member's pyproject.toml to read the lockfile.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/transaction_database/pyproject.toml packages/transaction_database/pyproject.toml
COPY services/transaction_service/pyproject.toml services/transaction_service/pyproject.toml
COPY services/agent_service/pyproject.toml services/agent_service/pyproject.toml
RUN uv sync --frozen --no-dev --package transaction-service --no-install-workspace

COPY packages packages
COPY services/transaction_service services/transaction_service
RUN uv sync --frozen --no-dev --package transaction-service

# Lambda runs the image as its own non-root user and only /tmp is writable.
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health
CMD ["uvicorn", "transaction_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
