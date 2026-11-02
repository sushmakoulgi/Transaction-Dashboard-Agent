ALEMBIC := uv run alembic -c packages/transaction_database/alembic.ini

.PHONY: install up up-stack up-stack-aurora logs up-phase2 dynamodb-local down reset-db migrate seed seed-daily aurora-bootstrap run-transaction-service run-agent-service lambda-package push-image rotate-tokens push-secrets ui-install ui-dev ui-test ui-api-types lint format test

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

# DynamoDB Local on localhost:8020 (session store tests; SESSION_DYNAMODB_ENDPOINT_URL for local runs).
dynamodb-local:
	docker compose --profile dynamodb up -d dynamodb-local

down:
	docker compose --profile phase2 --profile dynamodb down

# Deletes the database volume, so the role init script runs again on the next `make up`.
reset-db:
	docker compose down --volumes

migrate:
	$(ALEMBIC) upgrade head

seed:
	uv run python -m transaction_database.seed

# Demo data: 3-6 transactions on every day from FROM to today (replaces all rows).
# The integration tests expect the default `make seed` data.
seed-daily:
	@test -n "$(FROM)" || { echo "usage: make seed-daily FROM=2026-10-01"; exit 1; }
	SAMPLE_DATA_DAILY_FROM=$(FROM) uv run python -m transaction_database.seed

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

# A service's container image to its Amazon ECR repository, under a new tag each time (the
# repositories' tags are immutable). Prints the image URI to deploy.
#   make push-image SERVICE=transaction-service   (Lambda)   transaction_service.Dockerfile
#   make push-image SERVICE=agent-service         (ECS)      agent_service.Dockerfile
#   make push-image SERVICE=ui                    (ECS)      ui/Dockerfile (context ui/)
AWS_PROFILE_NAME ?= sushma
AWS_REGION_NAME ?= us-east-1
ECR_REGISTRY ?= 219806732458.dkr.ecr.$(AWS_REGION_NAME).amazonaws.com
ifndef IMAGE_TAG
IMAGE_TAG := $(shell git rev-parse --short HEAD)-$(shell date +%Y%m%d%H%M%S)
endif
SERVICE_IMAGE = $(ECR_REGISTRY)/$(SERVICE):$(IMAGE_TAG)
SERVICE_BUILD = $(if $(filter ui,$(SERVICE)),-f ui/Dockerfile ui,-f $(subst -,_,$(SERVICE)).Dockerfile .)

push-image:
	@test "$(SERVICE)" = ui -o \( -n "$(SERVICE)" -a -f "$(subst -,_,$(SERVICE)).Dockerfile" \) || { echo "usage: make push-image SERVICE=transaction-service|agent-service|ui"; exit 1; }
	aws ecr get-login-password --region $(AWS_REGION_NAME) --profile $(AWS_PROFILE_NAME) | docker login --username AWS --password-stdin $(ECR_REGISTRY)
	docker build --platform linux/arm64 --provenance=false -t $(SERVICE_IMAGE) $(SERVICE_BUILD)
	docker push $(SERVICE_IMAGE)
	@echo "Pushed $(SERVICE_IMAGE)"

# New random service tokens in .env (and the UI's in ui/.env.local); nothing is printed.
rotate-tokens:
	uv run python deploy/manage_secrets.py rotate-tokens

# ANTHROPIC_API_KEY from .env into the agent service's AWS Secrets Manager secret (ECS injects it).
push-secrets:
	uv run python deploy/manage_secrets.py push --profile $(AWS_PROFILE_NAME) --region $(AWS_REGION_NAME)

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
