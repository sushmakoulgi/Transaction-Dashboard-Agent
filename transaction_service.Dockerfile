# The Transaction Service as a container image for Amazon ECR: runs on AWS Lambda (container
# image) and on Amazon ECS / Fargate alike. Build from the repository root:
#
#   docker build --platform linux/arm64 --provenance=false -f transaction_service.Dockerfile -t transaction-service .
#
# --provenance=false: Lambda rejects images with a provenance attestation manifest.
#
# On Lambda, the Lambda Web Adapter (an extension in /opt/extensions) starts with the function,
# waits until /health answers, then turns each Function URL / API Gateway event into a plain
# HTTP request to uvicorn, so every FastAPI route works unchanged. Elsewhere (ECS, `docker run`)
# the adapter stays idle and uvicorn serves HTTP on port 8080 directly.
#
# Only the Transaction Service's own dependencies are installed (not the agent's), which keeps
# the image small and cold starts short. Settings come from environment variables (see
# .env.example); the image never contains .env (.dockerignore).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so source changes don't reinstall them. Only this service's workspace
# packages are copied: uv installs from the lockfile what they need, nothing of the others.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/transaction_database/pyproject.toml packages/transaction_database/pyproject.toml
COPY services/transaction_service/pyproject.toml services/transaction_service/pyproject.toml
RUN uv sync --frozen --no-dev --package transaction-service --no-install-workspace

COPY packages packages
COPY services/transaction_service services/transaction_service
RUN uv sync --frozen --no-dev --package transaction-service

# Non-root on ECS / Docker. (Lambda always runs images as its own non-root user, with only
# /tmp writable; the files above are world-readable, so both work.)
RUN useradd --create-home --uid 10001 app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health
EXPOSE 8080
CMD ["uvicorn", "transaction_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
