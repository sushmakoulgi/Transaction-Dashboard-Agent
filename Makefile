ALEMBIC := uv run alembic -c packages/transaction_database/alembic.ini

.PHONY: install up up-stack up-stack-aurora logs up-phase2 down reset-db migrate seed aurora-bootstrap run-transaction-service run-agent-service lambda-package ui-install ui-dev ui-test ui-api-types lint format test

install:
	uv sync

up:
	docker compose up -d --wait postgres

# The whole stack in containers (Postgres, setup, services, UI) at http://localhost:5173.
up-stack:
	docker compose up -d --build --wait

# The same stack against Amazon Aurora (see docker-compose.aurora.yml and .env.example).
up-stack-aurora:
	docker compose -f docker-compose.yml -f docker-compose.aurora.yml up -d --build --wait --scale postgres=0

logs:
	docker compose logs -f --tail=100

up-phase2:
	docker compose --profile phase2 up -d --wait

down:
	docker compose --profile phase2 down

# Deletes the database volume, so the role init script runs again on the next `make up`.
reset-db:
	docker compose down --volumes

migrate:
	$(ALEMBIC) upgrade head

seed:
	uv run python -m transaction_database.seed

# Once per Aurora cluster (DATABASE_IAM_AUTH=true): database + IAM-only roles. Then `make migrate seed`.
aurora-bootstrap:
	uv run python -m transaction_database.aurora_bootstrap

run-transaction-service:
	uv run uvicorn transaction_service.main:create_app --factory --port 8200 --reload

run-agent-service:
	uv run uvicorn agent_service.main:create_app --factory --port 8000 --reload

# The Transaction Service as an AWS Lambda zip (Python 3.12, arm64): locked dependencies as
# Linux wheels (Lambda python3.12 is Amazon Linux 2023, glibc 2.34), the workspace packages,
# precompiled. Handler: transaction_service.lambda_handler.handler
LAMBDA_BUILD := build/lambda
LAMBDA_TARGET := --target $(LAMBDA_BUILD)/package --python-platform aarch64-manylinux_2_28 --python-version 3.12 --compile-bytecode

lambda-package:
	rm -rf $(LAMBDA_BUILD) && mkdir -p $(LAMBDA_BUILD)/package
	uv export --frozen --no-dev --package transaction-service --no-emit-workspace --no-hashes --quiet -o $(LAMBDA_BUILD)/requirements.txt
	# The Lambda runtime already ships boto3; awscrt is only for `aws login` profiles. Leaving them
	# out keeps the zip under the 50 MB direct-upload limit and shortens cold starts.
	grep -v -E '^(boto3|botocore|s3transfer|jmespath|awscrt)==' $(LAMBDA_BUILD)/requirements.txt > $(LAMBDA_BUILD)/requirements-lambda.txt
	uv pip install --quiet $(LAMBDA_TARGET) --only-binary :all: --no-deps -r $(LAMBDA_BUILD)/requirements-lambda.txt
	uv pip install --quiet $(LAMBDA_TARGET) --no-deps packages/shared packages/transaction_database services/transaction_service
	cd $(LAMBDA_BUILD)/package && zip -qr ../transaction-service.zip .
	@du -sh $(LAMBDA_BUILD)/package $(LAMBDA_BUILD)/transaction-service.zip

ui-install:
	cd ui && npm install

ui-dev:
	cd ui && npm run dev

ui-test:
	cd ui && npm run typecheck && npm run lint && npm test

# Regenerate the UI's TypeScript types after changing the agent service API.
ui-api-types:
	uv run python -m agent_service.export_openapi > ui/src/api/agent-openapi.json
	cd ui && npm run generate:api-types

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest
