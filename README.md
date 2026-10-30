# Transaction-Dashboard-Agent
Dashboard agent to search payment transactions in natural language.

Ask questions like *"Which chargebacks did customer 10033317 have this month?"*. A Claude agent
turns them into safe, read-only searches through MCP tools, and the UI shows the answer with
a paginated, sortable results table.

## Quick start (everything in Docker)

```bash
cp .env.example .env     # set the passwords and tokens; add ANTHROPIC_API_KEY for real answers
make up-stack            # builds and starts Postgres, the services and the UI
open http://localhost:5173
```

The first start creates the schema and loads 100 sample transactions; later starts keep the
existing data (`make seed` replaces it on purpose). `make logs` follows the logs and
`make down` stops everything.

| Container | URL | What it is |
|---|---|---|
| ui | http://localhost:5173 | React UI (chat + results) |
| agent-service | http://localhost:8000/docs | Claude agent API used by the UI |
| mcp-server | http://localhost:8100/mcp | MCP tools, used only by the agent (internal; `MCP_TRANSPORT=in_process` runs them inside the agent instead) |
| transaction-service | http://localhost:8200/docs | Read-only search API over Postgres |
| postgres | localhost:5432 | Database (read-only role for the services) |

`AUTH_ENABLED=true` (the default) requires the bearer tokens from `.env` on every API.
Host ports can be changed with `*_HOST_PORT` variables (see `.env.example`).

## Developing on the host

`make up migrate seed` starts only Postgres; run the services with `make run-transaction-service`,
`make run-mcp-server`, `make run-agent-service` and `make ui-dev`, or with the VS Code launch
configurations. `make test`, `make lint` and `make ui-test` run the checks.

Design: [docs/HLD.md](docs/HLD.md) (architecture) and [docs/LLD.md](docs/LLD.md) (details
and task list).
