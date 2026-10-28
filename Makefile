ALEMBIC := uv run alembic -c packages/transaction_database/alembic.ini

.PHONY: install up up-stack logs up-phase2 down reset-db migrate seed run-transaction-service run-mcp-server run-agent-service ui-install ui-dev ui-test ui-api-types lint format test

install:
	uv sync

up:
	docker compose up -d --wait postgres

# The whole stack in containers (Postgres, setup, services, UI) at http://localhost:5173.
up-stack:
	docker compose up -d --build --wait

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

run-transaction-service:
	uv run uvicorn transaction_service.main:create_app --factory --port 8200 --reload

run-mcp-server:
	uv run uvicorn mcp_server.main:create_app --factory --port 8100 --reload

run-agent-service:
	uv run uvicorn agent_service.main:create_app --factory --port 8000 --reload

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
