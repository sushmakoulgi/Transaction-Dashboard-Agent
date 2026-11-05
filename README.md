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
| agent-service | http://localhost:8000/docs | Claude agent API used by the UI; runs the MCP tools in-process (no MCP endpoint, so outside MCP clients can't reach them) |
| transaction-service | http://localhost:8200/docs | Read-only search API over Postgres |
| postgres | localhost:5432 | Database (read-only role for the services) |

`AUTH_ENABLED=true` (the default) requires the bearer tokens from `.env` on every API.
Host ports can be changed with `*_HOST_PORT` variables (see `.env.example`).

## Developing on the host

`make up migrate seed` starts only Postgres; run the services with `make run-transaction-service`,
`make run-agent-service` and `make ui-dev`, or with the VS Code launch
configurations. `make test`, `make lint` and `make ui-test` run the checks.

## Using Amazon Aurora PostgreSQL instead of the local Postgres

The services can use an Aurora PostgreSQL cluster with IAM sign-in (e.g. an express-configuration
cluster, which has no passwords). Every new connection signs a 15-minute IAM token with your AWS
profile and uses SSL; the app roles `app_owner` / `app_readonly` sign in with tokens only.

1. Sign in to AWS (`aws login --profile <profile>`) and make sure the cluster is running.
2. In `.env`, point `POSTGRES_DSN_OWNER` / `POSTGRES_DSN_READONLY` at the cluster endpoint without
   a password and set `DATABASE_IAM_AUTH=true`, `DATABASE_AWS_REGION` and `DATABASE_AWS_PROFILE`
   (see `.env.example`).
3. Once per cluster: `make aurora-bootstrap migrate seed` (database, roles, schema, sample data).
4. Run on the host as usual (`make run-transaction-service`, ...) or in Docker with
   `make up-stack-aurora`, which mounts `~/.aws` into the database containers.

Set `DATABASE_IAM_AUTH=false` and restore the local URLs to go back to Docker Postgres. The
integration tests follow `.env`, so they run against whichever database it points at.

AWS deployment: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Design: [docs/HLD.md](docs/HLD.md) (architecture) and [docs/LLD.md](docs/LLD.md) (details
and task list).
