ALEMBIC := uv run alembic -c packages/transaction_database/alembic.ini

.PHONY: install up up-phase2 down reset-db migrate seed run-transaction-service run-mcp-server run-agent-service lint format test

install:
	uv sync

up:
	docker compose up -d --wait postgres

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

run-transaction-service:
	uv run uvicorn transaction_service.main:create_app --factory --port 8200 --reload

run-mcp-server:
	uv run uvicorn mcp_server.main:create_app --factory --port 8100 --reload

run-agent-service:
	uv run uvicorn agent_service.main:create_app --factory --port 8000 --reload

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest
