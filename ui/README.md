# Transaction Dashboard UI

React + TypeScript (Vite) front end for the agent service.

```bash
make ui-install            # once
cp ui/.env.example ui/.env.local   # set VITE_AGENT_API_TOKEN to AGENT_API_TOKEN from ../.env
make ui-dev                # http://localhost:5173 (the agent allows this origin by default)
make ui-test               # typecheck, lint and unit tests
```

The UI talks only to the agent service (`src/api/agentApiClient.ts`). Its types are generated
from the agent's OpenAPI schema; after changing the agent API, run `make ui-api-types` (a
backend test fails until the committed schema matches).

The API token is bundled into the page, so this setup is for local, single-user use only.
