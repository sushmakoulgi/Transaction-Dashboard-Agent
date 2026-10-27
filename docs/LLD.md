# Transaction Dashboard — Low-Level Design (LLD) & Task Breakdown

| | |
|---|---|
| Status | Draft v1 |
| Depends on | [HLD.md](HLD.md), [specs.md](specs.md) |
| Last updated | 2026-10-26 |

---

## 1. Repository layout

A `uv` workspace. Each service is its own package and depends only on `shared`, never on another service.

```
Transaction_Dashboard/
├── pyproject.toml                    # uv workspace root
├── docker-compose.yml
├── Makefile                          # up, down, migrate, seed, test, lint
├── .env.example
├── packages/
│   ├── transaction_database/         # used by migrations, seed and Transaction Service
│   │   ├── alembic.ini
│   │   └── src/transaction_database/
│   │       ├── orm_models.py         # Transaction, Payment, Refund, Chargeback, ChargebackEvent
│   │       ├── enums.py              # PaymentStatus, ChargebackStatus, ... (stored as text)
│   │       ├── migrations/           # Alembic env + versions/
│   │       └── seed/                 # sample_data_generator.py, __main__.py (make seed)
│   └── shared/src/shared/
│       ├── config/settings.py        # BaseServiceSettings, DatabaseSettings (pydantic-settings)
│       ├── logging/setup.py
│       ├── http/bearer_auth.py       # FastAPI dependency: verify_bearer_token
│       └── contracts/                # Pydantic DTOs shared across services
│           ├── filter_expression.py  # FilterCondition, FilterGroup, FilterNode
│           ├── search_request.py     # SearchRequest, SortSpec
│           ├── paginated_result.py   # PaginatedResult[T]
│           ├── query_plan.py         # QueryPlan, QueryPlanStep
│           └── validation_error.py   # FilterValidationIssue
├── services/
│   ├── transaction_service/src/transaction_service/
│   │   ├── main.py                   # app factory
│   │   ├── api/
│   │   │   ├── dependencies.py       # session + service wiring
│   │   │   ├── transaction_routes.py
│   │   │   ├── chargeback_routes.py
│   │   │   └── metadata_routes.py    # fields + validate
│   │   ├── persistence/
│   │   │   └── database.py           # async engine, read-only session factory
│   │   ├── filtering/
│   │   │   ├── operators.py          # Operator enum + OPERATOR_COMPILERS map
│   │   │   ├── field_types.py        # FieldType + value coercers
│   │   │   ├── filterable_field.py   # FilterableField, JoinStrategy
│   │   │   ├── field_registry.py     # FieldRegistry + per-entity registries
│   │   │   ├── relative_dates.py     # RelativeDateResolver (America/New_York)
│   │   │   ├── filter_validator.py   # FilterValidator
│   │   │   └── filter_compiler.py    # FilterCompiler: FilterNode → ColumnElement[bool]
│   │   ├── repositories/
│   │   │   ├── searchable_repository.py   # generic paginated search
│   │   │   ├── transaction_repository.py
│   │   │   └── chargeback_repository.py
│   │   ├── services/
│   │   │   ├── search_service.py     # validate → compile → query
│   │   │   └── transaction_detail_service.py
│   │   └── schemas/                  # response DTOs (TransactionSummary, TransactionDetail, ...)
│   ├── mcp_server/src/mcp_server/
│   │   ├── main.py                   # FastMCP app, streamable HTTP, auth
│   │   ├── transaction_service_client.py
│   │   └── tools/
│   │       ├── search_tools.py
│   │       ├── detail_tools.py
│   │       └── metadata_tools.py
│   └── agent_service/src/agent_service/
│       ├── main.py
│       ├── api/
│       │   ├── chat_routes.py
│       │   ├── plan_routes.py        # /plans/execute
│       │   └── transaction_detail_routes.py
│       ├── llm/
│       │   ├── claude_client.py      # wraps anthropic SDK (one place for model/effort config)
│       │   └── system_prompt.py
│       ├── mcp_bridge/
│       │   ├── mcp_tool_gateway.py   # connect, list_tools, call_tool
│       │   └── tool_definition_mapper.py  # MCP Tool → Anthropic tool param
│       ├── memory/
│       │   ├── session_store.py      # SessionStore Protocol
│       │   └── in_memory_session_store.py
│       ├── agent/
│       │   ├── query_agent.py        # tool-use loop
│       │   └── query_plan_recorder.py
│       ├── plan_execution/
│       │   └── plan_executor.py      # runs a QueryPlan against Transaction Service
│       └── plan_resolution/          # Phase 2
│           ├── plan_resolver.py      # PlanResolver Protocol + ChainedPlanResolver
│           ├── query_template_extractor.py
│           ├── slot_substitutor.py
│           ├── embedding_provider.py # Protocol + VoyageEmbeddingProvider
│           ├── vector_plan_store.py  # ChromaVectorPlanStore
│           └── redis_plan_cache.py
├── database/
│   └── initdb/01_create_application_roles.sh  # app_owner + app_readonly (runs on first Postgres start)
├── ui/                               # React + Vite + TS
└── tests/                            # per-service unit + integration, e2e
```

## 2. Database

### 2.1 DDL highlights
```sql
CREATE TABLE transactions (
    transaction_id    uuid PRIMARY KEY,
    customer_id       text        NOT NULL,
    amount            numeric(12,2) NOT NULL,
    currency          char(3)     NOT NULL,
    transaction_type  text        NOT NULL,          -- PURCHASE, SUBSCRIPTION, ...
    transaction_date  timestamptz NOT NULL,
    merchant_details  jsonb       NOT NULL DEFAULT '{}',
    metadata          jsonb       NOT NULL DEFAULT '{}',
    UNIQUE (transaction_id, customer_id)             -- target for composite FKs
);
CREATE TABLE payments (
    payment_id        uuid PRIMARY KEY,
    transaction_id    uuid NOT NULL UNIQUE,
    customer_id       text NOT NULL,
    payment_method    text NOT NULL,                 -- CARD, WALLET, ACH
    payment_status    text NOT NULL,                 -- AUTHORIZED, CAPTURED, VOIDED, FAILED
    authorized_amount numeric(12,2), captured_amount numeric(12,2),
    authorized_at     timestamptz,   captured_at     timestamptz,
    payment_details   jsonb NOT NULL DEFAULT '{}',
    UNIQUE (payment_id, customer_id),
    FOREIGN KEY (transaction_id, customer_id) REFERENCES transactions (transaction_id, customer_id)
);
CREATE TABLE refunds (
    refund_id           uuid PRIMARY KEY,
    original_payment_id uuid NOT NULL,
    customer_id         text NOT NULL,
    refund_amount numeric(12,2) NOT NULL, refund_status text NOT NULL,
    refunded_at timestamptz NOT NULL, refund_details jsonb NOT NULL DEFAULT '{}',
    FOREIGN KEY (original_payment_id, customer_id) REFERENCES payments (payment_id, customer_id)
);
CREATE TABLE chargebacks (
    chargeback_id     uuid PRIMARY KEY,
    transaction_id    uuid NOT NULL UNIQUE,
    customer_id       text NOT NULL,
    reason_code text NOT NULL, chargeback_amount numeric(12,2) NOT NULL,
    chargeback_status text NOT NULL, opened_at timestamptz NOT NULL,
    dispute_details jsonb NOT NULL DEFAULT '{}',
    FOREIGN KEY (transaction_id, customer_id) REFERENCES transactions (transaction_id, customer_id)
);
CREATE TABLE chargeback_events (
    chargeback_event_id uuid PRIMARY KEY,
    chargeback_id  uuid NOT NULL REFERENCES chargebacks,
    event_type text NOT NULL, event_sequence int NOT NULL,
    event_date timestamptz NOT NULL, event_payload jsonb NOT NULL DEFAULT '{}',
    UNIQUE (chargeback_id, event_sequence)
);
-- Composite FKs guarantee a child's customer_id equals its parent's.
-- Indexes: transactions(customer_id), transactions(transaction_date), payments(customer_id),
-- chargebacks(customer_id, opened_at), refunds(customer_id), refunds(original_payment_id),
-- chargeback_events(chargeback_id),
-- GIN on payments.payment_details, transactions.merchant_details.
```

### 2.2 Roles
- `app_owner` runs the migrations.
- `app_readonly` has `GRANT SELECT ON ALL TABLES`, `ALTER ROLE app_readonly SET default_transaction_read_only = on` and `SET statement_timeout = '5s'`.
- The Transaction Service connects **only** as `app_readonly`.

### 2.3 Seed data (`transaction_database.seed`)
- Fixed Faker seed, so every run produces the same data.
- 100 transactions spread over the last 90 days, with about 30% in the current month (America/New_York). Uses 15 customer IDs (`C-1001`…`C-1015`), so each customer has several transactions.
- 1 payment per transaction, in a realistic status mix.
- About 20% of payments have 1–2 refunds.
- About 15% of transactions have a chargeback, about half of those opened this month. Each chargeback has 1–4 events in order.
- JSON payloads, for example:
  - `payment_details`: `{card_brand, card_last4, wallet_type, processor_response_code}`
  - `merchant_details`: `{merchant_id, merchant_name, merchant_category, country}`
  - `dispute_details`: `{network, network_case_id, evidence_due_date}`

## 3. Filter engine (Transaction Service)

### 3.1 Filter expression contract (`shared/contracts/filter_expression.py`)
```python
class Operator(StrEnum):
    EQUALS = "eq"
    NOT_EQUALS = "neq"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    CONTAINS = "contains"  # case-insensitive substring
    IS_NULL = "is_null"
    EXISTS = "exists"  # exists: boolean relation flags


class RelativeDateValue(BaseModel):
    relative: Literal["today", "yesterday", "this_month", "last_month", "this_year"] | None = None
    last_n_days: int | None = Field(None, ge=1, le=366)


class FilterCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["condition"] = "condition"
    field: str
    operator: Operator
    value: Any = None  # scalar | list | RelativeDateValue


class FilterGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["group"] = "group"
    logic: Literal["and", "or"] = "and"
    negate: bool = False  # NOT (...)
    children: list["FilterNode"] = Field(min_length=1)


FilterNode = Annotated[FilterCondition | FilterGroup, Field(discriminator="kind")]


class SortSpec(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "desc"


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filter: FilterNode | None = None
    sort: SortSpec | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=100)
```

Example: *"Transactions for C-1004 this month that are either refunded or over $500, but not Amex"*
```json
{
  "filter": {"kind": "group", "logic": "and", "children": [
    {"kind": "condition", "field": "customer_id", "operator": "eq", "value": "C-1004"},
    {"kind": "condition", "field": "transaction_date", "operator": "between", "value": {"relative": "this_month"}},
    {"kind": "group", "logic": "or", "children": [
      {"kind": "condition", "field": "has_refund", "operator": "exists", "value": true},
      {"kind": "condition", "field": "amount", "operator": "gt", "value": 500}
    ]},
    {"kind": "group", "logic": "and", "negate": true, "children": [
      {"kind": "condition", "field": "card_brand", "operator": "eq", "value": "AMEX"}
    ]}
  ]},
  "page": 1, "page_size": 25
}
```

### 3.2 Filter registry (`filterable_field.py`, `field_registry.py`)
```python
class FieldType(StrEnum):
    STRING, DECIMAL, INTEGER, TIMESTAMP, ENUM, BOOLEAN, UUID


class JoinStrategy(StrEnum):
    BASE = "base"  # column on the root table
    ONE_TO_ONE = "1:1"  # LEFT JOIN (payments, chargebacks)
    ONE_TO_MANY = "1:n"  # correlated EXISTS subquery (refunds, chargeback_events)


@dataclass(frozen=True)
class FilterableField:
    public_name: str
    description: str  # shown to the LLM via list_filterable_fields
    field_type: FieldType
    allowed_operators: frozenset[Operator]
    column_resolver: Callable[
        [], ColumnElement
    ]  # e.g. lambda: Payment.payment_details["card_brand"].astext
    join_strategy: JoinStrategy = JoinStrategy.BASE
    relation_path: tuple[RelationshipStep, ...] = ()
    enum_values: tuple[str, ...] = ()
    is_sortable: bool = False


class FieldRegistry:
    def __init__(self, entity_name: str, fields: Iterable[FilterableField]): ...
    def get_field(self, public_name: str) -> FilterableField | None: ...
    def suggest_similar(self, unknown_name: str) -> list[str]: ...  # difflib
    def describe_fields(self) -> list[FilterableFieldDescription]: ...
```
- **Transaction registry (initial):** `transaction_id`, `customer_id`, `amount`, `currency`, `transaction_type`, `transaction_date`, `merchant_category` (JSON), `merchant_name` (JSON), `merchant_country` (JSON), `payment_id`, `payment_status`, `payment_method`, `card_brand` (JSON), `has_chargeback`, `chargeback_status`, `chargeback_opened_at`, `chargeback_reason_code`, `has_refund`, `refund_status`, `refunded_at`.
- **Chargeback registry:** `chargeback_id`, `transaction_id`, `customer_id` (own column), `reason_code`, `chargeback_status`, `chargeback_amount`, `opened_at`, `network` (JSON), `chargeback_event_type` (1:n), `chargeback_event_date` (1:n).
- **Adding a filter:** add one `FilterableField(...)` to the right registry. No other code changes (Open/Closed).

### 3.3 Validator (`filter_validator.py`)
`FilterValidator.validate(entity_registry, search_request) -> list[FilterValidationIssue]`. It walks the tree and collects **all** issues before returning:
- Unknown field, with suggestions.
- Operator not allowed for the field.
- Value does not fit the type (coerced by `FieldType`), or an enum value is not in `enum_values`.
- `between` needs exactly 2 values or a `RelativeDateValue`. `in` / `not_in` take 1–50 values.
- Nesting deeper than `MAX_FILTER_DEPTH = 3`, or more than `MAX_FILTER_CONDITIONS = 20` conditions.
- Sort field is not sortable.

```python
class FilterValidationIssue(BaseModel):
    path: str  # "filter.children[2].children[0].field"
    code: str  # UNKNOWN_FIELD, OPERATOR_NOT_ALLOWED, INVALID_VALUE, TOO_DEEP, ...
    message: str
    suggestions: list[str] = []
```

### 3.4 Compiler (`filter_compiler.py`)
- `FilterCompiler.compile(node, registry) -> CompiledFilter(where_clause, required_joins)`.
- `OPERATOR_COMPILERS: dict[Operator, Callable[[ColumnElement, Any], ColumnElement[bool]]]` maps each operator to a function, e.g. `{EQUALS: lambda col, v: col == v, IN: lambda col, v: col.in_(v), CONTAINS: lambda col, v: col.ilike(f"%{escape_like(v)}%"), ...}`. Values are always bound parameters.
- A group compiles to `and_(*children)` / `or_(*children)`, wrapped in `not_()` when `negate` is set.
- `ONE_TO_MANY` fields compile to `exists(select(1).where(<correlation>, <predicate>))`. This avoids duplicate rows.
- `RelativeDateResolver(timezone="America/New_York", clock=Clock)` turns `{"relative": "this_month"}` into `[month_start, next_month_start)` in UTC. The clock is injected so tests can control time.

### 3.5 Generic search (`searchable_repository.py`, `search_service.py`)
```python
class SearchableRepository(Generic[RowT]):
    def __init__(self, root_entity, base_query_factory, row_mapper, registry, compiler): ...
    async def search(self, session, request: SearchRequest) -> PaginatedResult[RowT]:
        # base select → apply joins → where → count(*) over same where → order/limit/offset
```
- `SearchService.search(entity, request)` runs: validator, then raise `FilterValidationFailed(issues)` (HTTP 422), then repository.
- Default sort: `transaction_date desc` / `opened_at desc`, then the primary key to break ties, so page order is stable.

```python
class PaginatedResult(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total_count: int
    total_pages: int
```

## 4. Transaction Service API (`:8200`, prefix `/api/v1`, bearer auth)

| Method | Path | Body / params | Returns |
|---|---|---|---|
| POST | `/transactions/search` | `SearchRequest` | `PaginatedResult[TransactionSummary]` |
| POST | `/chargebacks/search` | `SearchRequest` | `PaginatedResult[ChargebackSummary]` |
| GET | `/transactions/{transaction_id}` | — | `TransactionDetail` (payment, refunds[], chargeback + events[]) |
| GET | `/{entity}/filterable-fields` | `entity ∈ {transactions, chargebacks}` | `list[FilterableFieldDescription]` |
| POST | `/{entity}/filters/validate` | `SearchRequest` | `{is_valid, issues[]}` |
| GET | `/health` | — | `{status}` |

- Search uses `POST` only so the filter tree can be sent as a body. No endpoint changes data.
- Errors are 422 `{"error": "FILTER_VALIDATION_FAILED", "issues": [...]}`, or 404 for an unknown ID.

**Use cases → requests:**

| UC | Tool | Filter |
|---|---|---|
| 1 Transactions on a date | `search_transactions` | `transaction_date between [d 00:00, d+1 00:00)` (ET) |
| 2 Transactions with chargebacks this month | `search_transactions` | `chargeback_opened_at between {relative: this_month}` |
| 3 By customer | `search_transactions` | `customer_id eq X` |
| 4 By payment | `search_transactions` | `payment_id eq X` |
| 5 Customer's chargebacks this month | `search_chargebacks` | `customer_id eq X AND opened_at between {relative: this_month}` |

## 5. MCP Server (`:8100`, Streamable HTTP at `/mcp`)

### 5.1 Tools
| Tool | Input | Maps to |
|---|---|---|
| `search_transactions` | `SearchRequest` | `POST /transactions/search` |
| `search_chargebacks` | `SearchRequest` | `POST /chargebacks/search` |
| `get_transaction_detail` | `{transaction_id}` | `GET /transactions/{id}` |
| `list_filterable_fields` | `{entity}` | `GET /{entity}/filterable-fields` |
| `validate_filters` | `{entity, request}` | `POST /{entity}/filters/validate` |

- Input schemas come from the shared Pydantic contracts, so the MCP server, the agent and the Transaction Service always use the same definitions.
- Tool descriptions spell out the filter language, the relative-date values, and "call `list_filterable_fields` when unsure of a field name".
- A 422 from the Transaction Service becomes an MCP tool error containing the issue list, so outside clients get the same chance to correct themselves.
- Auth: `Authorization: Bearer <MCP_API_TOKEN>` is checked by middleware. The token is shared by the agent and outside clients (single user).
- `TransactionServiceClient` is one `httpx.AsyncClient` wrapper with timeouts and retries, and is the only HTTP code in this service.

## 6. Agent Service (`:8000`, prefix `/api`)

### 6.1 Endpoints
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/chat` | `{session_id?, message}` | `ChatResponse{session_id, reply, query_plan?, result_page?}` |
| POST | `/plans/execute` | `{query_plan, page, page_size?}` | `PaginatedResult[...]` |
| GET | `/transactions/{id}` | — | `TransactionDetail` (proxy, used by the Phase 2 drawer) |
| GET | `/sessions/{id}/entries` | — | stored questions with their query plans |
| DELETE | `/sessions/{id}` | — | clear session |

```python
class QueryPlanStep(BaseModel):
    tool_name: Literal["search_transactions", "search_chargebacks", "get_transaction_detail"]
    arguments: dict  # SearchRequest without page fields, or {transaction_id}


class QueryPlan(BaseModel):
    steps: list[QueryPlanStep]  # executed in order; last search step drives the table
    result_entity: Literal["transactions", "chargebacks"]
```

### 6.2 Claude client (`llm/claude_client.py`)
- One class owns the model IDs and request settings, read from config:
  - `PLANNER_MODEL=claude-opus-5-5`, `output_config.effort="medium"`, set explicitly (the default for this model).
  - Adaptive thinking.
  - `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) for refusals.
- Always check `stop_reason`, and handle `refusal` and `max_tokens`.
- `tool_choice` is `auto`. Forced tool choice isn't supported on this model, so the system prompt steers tool selection.
- Errors are caught most specific first: `RateLimitError` → retry with backoff, `APIStatusError` → surface to the user, `APIConnectionError` → retry.

### 6.3 Agent loop (`agent/query_agent.py`)
```python
class QueryAgent:
    def __init__(
        self,
        claude_client,
        tool_gateway: McpToolGateway,
        session_store: SessionStore,
        plan_recorder_factory,
        max_tool_iterations: int = 8,
    ): ...

    async def answer(self, session_id: str, user_question: str) -> AgentAnswer:
        previous_entries = await self.session_store.load_recent_entries(session_id)
        turn_messages = [user_turn(render_question_with_context(previous_entries, user_question))]
        recorder = self.plan_recorder_factory()
        for _ in range(self.max_tool_iterations):
            response = await self.claude_client.create_message(
                turn_messages, self.tool_gateway.tool_definitions
            )
            turn_messages.append(assistant_turn(response.content))
            if response.stop_reason != "tool_use":
                break
            tool_results = await self._run_tool_calls(
                response, recorder
            )  # parallel; one user message
            turn_messages.append(user_turn(tool_results))
        query_plan = recorder.build_plan()
        await self.session_store.append_entry(
            session_id, SessionEntry(user_question=user_question, query_plan=query_plan)
        )
        return AgentAnswer(
            reply=final_text(response),
            query_plan=query_plan,
            result_page=recorder.last_successful_page(),
        )
        # turn_messages (tool results, replies) goes out of scope here — never persisted
```
- **Context message:** `render_question_with_context` builds one user message: `<previous_questions>` (numbered, oldest first, each with the tool and filters it used) followed by `<current_question>`. Earlier entries are labelled as context only, so Claude acts on the current question.
- **Tool result sent to Claude:** `total_count`, `page`, and up to 10 rows with only the key columns. The full page goes to the UI and is never fed back into the LLM context.
- **Validation errors:** returned to Claude as `tool_result` with `is_error: true` and the issue list. This is R6.
- **`QueryPlanRecorder`:** records each **successful** call to a search or detail tool in order. These calls become the `QueryPlan`.
- **System prompt:** today's date and the business timezone, the filter-language rules, "prefer relative date values", "resolve pronouns from conversation context", and "never claim results without calling a tool". It is kept unchanged across requests so it can be cached. The date goes in a mid-conversation system message, not the cached prefix.

### 6.4 Session memory (`memory/`)
```python
class SessionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_question: str
    query_plan: QueryPlan | None  # tool names + filters only; None if no search ran


class SessionStore(Protocol):
    async def load_recent_entries(self, session_id: str) -> list[SessionEntry]: ...
    async def append_entry(self, session_id: str, entry: SessionEntry) -> None: ...
    async def delete_session(self, session_id: str) -> None: ...
```
- **The store holds user questions and their query plans only.** It never stores query results, tool results or assistant replies. `SessionEntry` forbids extra fields, and `QueryPlan` holds only tool names and filter arguments, so result rows can't be stored by mistake.
- The stored plan lets Claude narrow an earlier search ("of those, which had refunds?"): it takes the previous filter and adds a condition.
- `load_recent_entries` returns the last `SESSION_CONTEXT_QUESTION_LIMIT` entries (default 10).
- **Phase 1:** `InMemorySessionStore`, a dict of `deque(maxlen=limit)` plus an `asyncio.Lock`, with idle sessions expiring after `SESSION_IDLE_TTL_MINUTES`.
- Each turn's Claude conversation is built fresh and thrown away when the turn ends. No assistant turns are replayed across turns, so no compaction is needed.
- `RedisSessionStore` can replace it later with no other changes (Liskov / Dependency Inversion).
- The UI keeps its own on-screen transcript (questions and replies) in browser state for display. The server never stores it.

### 6.5 MCP bridge (`mcp_bridge/`)
- `McpToolGateway` opens a Streamable HTTP client session to `MCP_SERVER_URL` with the bearer token at startup (FastAPI lifespan). It caches `list_tools()` and exposes `call_tool(name, args)`.
- `ToolDefinitionMapper` converts an MCP `Tool` to `{name, description, input_schema}` for the Anthropic API. The tool list is sorted, so the prompt prefix stays identical and cacheable.

### 6.6 Plan executor (`plan_execution/plan_executor.py`)
- `PlanExecutor.execute(plan, page, page_size)` runs the steps in order against the Transaction Service over HTTP (the same `httpx` client pattern as the MCP server). Page and page_size are applied to the last search step.
- Used by `/plans/execute` for paging, and in Phase 2 for cache or RAG hits.

## 7. Phase 2 — Plan resolution

### 7.1 Interfaces
```python
class PlanResolver(Protocol):
    async def resolve(self, query: ExtractedQuery) -> ResolvedPlan | None: ...


class ChainedPlanResolver:  # Chain of Responsibility
    def __init__(
        self, resolvers: list[PlanResolver]
    ): ...  # [RedisPlanCache, ChromaVectorPlanStore]


class EmbeddingProvider(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
```

### 7.2 Template extraction (`query_template_extractor.py`)
- Uses `claude-haiku-5-5` with structured output (`output_config.format`). It receives the user message plus the entities currently in the session context.
- It returns:
  ```python
  class ExtractedQuery(BaseModel):
      template_text: str  # "chargebacks opened this month for customer {customer_id}"
      slot_values: dict[str, str]  # {"customer_id": "C-1004"}
      is_self_contained: bool  # False if a pronoun can't be resolved → skip cache, use agent
  ```
- Relative dates stay in the template as words ("this month"). They are not slots, because the Transaction Service resolves them.

### 7.3 Stored plan
```python
class StoredPlanTemplate(BaseModel):
    template_id: str  # sha256(normalized template_text)
    template_text: str
    slot_names: list[str]
    plan_template: QueryPlan  # argument values may be {"$slot": "customer_id"}
    created_at: datetime
    hit_count: int
    last_used_at: datetime
```
- **Chroma** (`collection="query_plan_templates"`, cosine distance): the document is `template_text`, the embedding comes from Voyage, and the metadata holds `template_id`, `slot_names` and the JSON plan.
- **Redis:** key `plan_template:{template_id}` holds the JSON. TTL is `PLAN_CACHE_TTL_SECONDS` (default 86400; `0` means no expiry). The TTL is refreshed on every hit.

### 7.4 Resolution algorithm
1. Run `extract(message, session_context)`. If it isn't self-contained, go to the full agent.
2. Check Redis for `GET plan_template:{sha256(template)}`. A hit means steps 3 and 4 are skipped.
3. Embed the template with Voyage and run a Chroma `query(n_results=3)`. Accept the top result if `similarity ≥ PLAN_SIMILARITY_THRESHOLD` (default 0.90) **and** `slot_names ⊆ slot_values.keys()`.
4. On a hit, write the plan to Redis (promote it).
5. `SlotSubstitutor.substitute(plan_template, slot_values)` replaces `{"$slot": ...}` markers; a missing slot is an error. Then `validate_filters` runs for each step. If anything fails, go to the full agent.
6. `PlanExecutor` runs the plan. The reply is a fixed template, e.g. "Found {total} chargebacks for {customer_id} opened this month." The turn is appended to session history.
7. **Miss path:** the full agent runs. If the recorded plan succeeded, the agent's slot values in the arguments are replaced with `{"$slot": ...}` markers (matched by value against `slot_values`). The template is then stored in Chroma and Redis. Plans are stored by a background task so the response isn't delayed.

## 8. UI (React + Vite + TS)

```
ui/src/
├── api/            agentApiClient.ts (fetch wrappers), types.ts (mirrors contracts)
├── features/chat/  ChatPanel.tsx, MessageList.tsx, ChatInput.tsx, useChatSession.ts
├── features/results/ ResultsTable.tsx (TanStack Table, columns per entity), Pager.tsx, usePlanResults.ts
├── features/detail/  TransactionDetailDrawer.tsx (Phase 2), ChargebackTimeline.tsx
└── App.tsx         split layout: chat left, results right
```
- The current `query_plan` is kept in state. `usePlanResults(plan, page)` calls `/plans/execute`; TanStack Query caches pages.
- Columns are defined by `result_entity` (a column registry per entity, mirroring the backend filter registry).
- The `session_id` is stored in `sessionStorage` and sent with each chat request.
- The on-screen chat transcript lives only in browser state. The server keeps user questions and their query plans only (§6.4).

## 9. Configuration (`.env.example`)
```
POSTGRES_DSN_READONLY=postgresql+asyncpg://app_readonly:***@postgres:5432/transactions
POSTGRES_DSN_OWNER=postgresql+psycopg://app_owner:***@postgres:5432/transactions
TRANSACTION_SERVICE_URL=http://transaction-service:8200
TRANSACTION_SERVICE_API_TOKEN=***
MCP_SERVER_URL=http://mcp-server:8100/mcp
MCP_API_TOKEN=***
ANTHROPIC_API_KEY=***
PLANNER_MODEL=claude-opus-5-5
PLANNER_EFFORT=medium
TEMPLATE_EXTRACTOR_MODEL=claude-haiku-5-5
MAX_TOOL_ITERATIONS=8
BUSINESS_TIMEZONE=America/New_York
SESSION_IDLE_TTL_MINUTES=120
SESSION_CONTEXT_QUESTION_LIMIT=10
# Phase 2
VOYAGE_API_KEY=***
EMBEDDING_MODEL=<voyage model id>
CHROMA_URL=http://chroma:8000
REDIS_URL=redis://redis:6379/0
PLAN_CACHE_TTL_SECONDS=86400
PLAN_SIMILARITY_THRESHOLD=0.90
```

## 10. SOLID / code conventions

| Principle | Where |
|---|---|
| Single Responsibility | Validator, compiler, repository, service, route are separate classes. Routes only translate HTTP |
| Open/Closed | New filter = registry entry. New operator = one `OPERATOR_COMPILERS` entry. New resolver = add it to the chain |
| Liskov | `SessionStore`, `PlanResolver`, `EmbeddingProvider` implementations are interchangeable |
| Interface Segregation | Small Protocols (e.g. `PlanCache` only `get`/`put`) |
| Dependency Inversion | Services depend on Protocols. Concrete classes are wired in `api/dependencies.py` / lifespan |
| DRY | Shared contracts package; one HTTP client wrapper per service; one generic `SearchableRepository` |

**Conventions:**
- Names are full words (`chargeback_opened_at`, not `cb_dt`).
- Type hints everywhere; `ruff` + `mypy --strict`.
- No module-level mutable state except the DI container.
- Docstrings only where the name is not enough.

## 11. Testing strategy

| Level | What |
|---|---|
| Unit | Validator (each issue code), compiler (generated SQL snapshots), relative dates (frozen clock, month and DST boundaries), slot substitutor, tool mapper |
| Security | Injection payloads in field names, values, JSON paths and sort fields (`"; DROP TABLE"`, `' OR 1=1 --`, `customer_id) OR (1=1`). All are rejected or bound safely. An attempted write inside a read-only session fails |
| Integration | Transaction Service against the real Postgres in docker-compose with seed data. All 5 use cases plus OR/NOT/nested cases |
| Contract | MCP tool schemas match the shared Pydantic contracts |
| Agent | Claude client stubbed with scripted responses to test the loop, error retry and plan recording. A small set of live tests (`-m live`) for natural-language → plan |
| E2E | Playwright: ask a question → table shows → page 2 → (P2) open detail drawer |

---

## 12. Task breakdown

Size estimates: **S** ≤ ½ day, **M** ≈ 1 day, **L** ≈ 2 days. Each task's acceptance criteria are its definition of done.

### Phase 0 — Foundation
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P0-1 | Initialize uv workspace, `shared` package, ruff/mypy/pytest config, Makefile | S | — | `make lint test` passes on an empty skeleton |
| P0-2 | docker-compose: postgres, redis, chroma (P2 services present but optional), healthchecks | S | P0-1 | `make up` brings all services up healthy |
| P0-3 | `shared/config` settings classes + `.env.example` | S | P0-1 | Each service loads typed settings. A missing required var fails fast |
| P0-4 | ORM models + Alembic initial migration (5 tables, constraints, indexes) | M | P0-2 | `make migrate` creates the schema. UNIQUE constraints enforce the 1:1 relationships |
| P0-5 | `database/initdb` roles script: `app_owner`, `app_readonly`, read-only defaults, statement timeout | S | P0-4 | `INSERT` as `app_readonly` fails. `SELECT` works |
| P0-6 | Seed script (deterministic, distributions per §2.3) | M | P0-4 | `make seed` gives 100 transactions, matching payments, refunds about 20%, chargebacks about 15% with events. Re-running produces the same data |

### Phase 1a — Transaction Service
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P1a-1 | Shared contracts: `FilterNode`, `SearchRequest`, `PaginatedResult`, `FilterValidationIssue` | S | P0-1 | JSON schema generated. Unknown keys rejected |
| P1a-2 | `FieldType` coercers + `Operator` enum | S | P1a-1 | Unit tests per type (decimal, timestamp, enum, uuid, bool) |
| P1a-3 | `FilterableField`, `FieldRegistry` (+ suggestions) and the transaction / chargeback registries, including JSON-path fields | M | P1a-2 | Registries list every field in §3.2. `suggest_similar("cust_id")` → `customer_id` |
| P1a-4 | `RelativeDateResolver` with injected clock | S | P1a-1 | `this_month` / `last_month` / `last_n_days` correct across month and DST boundaries in ET |
| P1a-5 | `FilterValidator` (tree walk, depth and count limits, all issue codes) | M | P1a-3 | Every issue code covered. All issues returned in one pass, with the right path |
| P1a-6 | `FilterCompiler` (operator map, AND/OR/NOT, 1:1 joins, 1:n EXISTS) | L | P1a-3, P1a-4 | SQL snapshot tests. No string interpolation of values. OR + NOT + nested compile correctly |
| P1a-7 | Read-only async DB session factory | S | P0-5 | Sessions use `app_readonly`. An attempted write raises |
| P1a-8 | `SearchableRepository` + transaction/chargeback repositories (count + page, stable sort) | M | P1a-6, P1a-7 | Correct `total_count` with 1:n filters (no duplicates) |
| P1a-9 | `SearchService` + `TransactionDetailService` (full aggregate) | M | P1a-5, P1a-8 | Detail includes payment, refunds[], chargeback, events[] ordered by sequence |
| P1a-10 | Routes, 422/404 error mapping, bearer auth, `/health` | M | P1a-9 | OpenAPI docs render. Validation errors return the issue list |
| P1a-11 | Integration tests for UC1–UC5 + OR/NOT cases | M | P1a-10, P0-6 | All pass against seeded Postgres |
| P1a-12 | Security test suite (injection, sort-field abuse, oversized filters) | M | P1a-10 | All payloads rejected or bound. DB unchanged |

### Phase 1b — MCP Server
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P1b-1 | `TransactionServiceClient` (httpx, timeouts, retries, token) | S | P1a-10 | Unit tested with respx |
| P1b-2 | FastMCP app, Streamable HTTP transport, bearer-token middleware | S | P0-3 | Unauthenticated requests rejected |
| P1b-3 | 5 tools with detailed descriptions; schemas from shared contracts; 422 → tool error with issues | M | P1b-1, P1b-2 | MCP Inspector lists the tools and runs each successfully |
| P1b-4 | Contract tests + doc for connecting Claude Desktop / other clients | S | P1b-3 | README section verified with a real client |

### Phase 1c — Agent Service
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P1c-1 | `ClaudeClient` (model/effort config, adaptive thinking, fallbacks, stop-reason handling, typed error handling) | M | P0-3 | Unit tests with stubbed SDK cover refusal, max_tokens and rate-limit paths |
| P1c-2 | `McpToolGateway` + `ToolDefinitionMapper` (lifespan connect, sorted tool list) | M | P1b-3 | Agent lists MCP tools at startup. Mapped schemas are accepted by the API |
| P1c-3 | `SessionEntry`, `SessionStore` Protocol + `InMemorySessionStore` (question + plan only, last-N limit, idle TTL) | S | P0-1 | A test checks that no tool result, result row or reply is persisted after a turn. Expiry works |
| P1c-4 | System prompt (stable prefix) + date as a mid-conversation system message | S | P1c-1 | Prompt-cache reads > 0 on the second turn |
| P1c-5 | `QueryAgent` loop: parallel tool calls, compact tool results, error tool_results, max iterations | L | P1c-1..4 | Scripted tests: success, validation-retry, iteration cap |
| P1c-6 | `QueryPlanRecorder` + `PlanExecutor` | M | P1c-5 | Re-running a recorded plan returns the same page 1 as the agent did |
| P1c-7 | Routes: `/chat`, `/plans/execute`, `/transactions/{id}`, sessions; CORS for UI | M | P1c-6 | OpenAPI. Session ID created when absent |
| P1c-9 | Live natural-language eval (~25 prompts covering UC1–5, follow-ups, OR/NOT phrasing) | M | P1c-7 | ≥ 90% choose the correct tool and filters |

### Phase 1d — UI
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P1d-1 | Vite + React + TS scaffold, API client, types mirroring contracts | S | P1c-7 | `npm run dev` connects to the agent |
| P1d-2 | Chat panel (session in sessionStorage, loading/error states) | M | P1d-1 | Conversation persists across page reloads within the tab |
| P1d-3 | Results table (column registry per entity) + pager via `/plans/execute` | M | P1d-1 | Paging makes no `/chat` call (checked in the network tab / tests) |
| P1d-4 | docker-compose `ui` service + Playwright E2E for UC1–5 | M | P1d-2, P1d-3 | E2E passes locally |

### Phase 2a — Plan RAG
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P2a-1 | `EmbeddingProvider` Protocol + `VoyageEmbeddingProvider` | S | P0-3 | Query vs document input types used correctly. Retries on rate limit |
| P2a-2 | `QueryTemplateExtractor` (Haiku, structured output, session-context aware) | M | P1c-1 | Extraction tests: slots found; pronoun without context → `is_self_contained=False` |
| P2a-3 | `StoredPlanTemplate` + `SlotSubstitutor` (+ de-substitution of a recorded plan) | M | P1c-6 | Round trip: plan → template → substituted plan is identical |
| P2a-4 | `ChromaVectorPlanStore` (collection, upsert by template_id, threshold query) | M | P2a-1, P2a-3 | Similar phrasings hit; different intents miss at 0.90 |
| P2a-5 | Plan resolution wiring in `/chat`: extract → resolve → validate → execute; background store on miss | L | P2a-2..4 | A repeated query shape makes no Opus call (verified by stubbed client counters) |

### Phase 2b — Redis cache
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P2b-1 | `RedisPlanCache` (get/put, configurable TTL, TTL refresh on hit, hit counter) | S | P0-2 | TTL honored; `0` means no expiry |
| P2b-2 | `ChainedPlanResolver` [Redis → Chroma]; promote Chroma hits to Redis | S | P2a-5, P2b-1 | A Redis hit makes no Voyage or Chroma call |
| P2b-3 | Observability: log which tier answered (`redis` / `vector` / `llm`) with latency | S | P2b-2 | Visible in logs. Counts exposed on `/health/details` |

### Phase 2c — Drill-down
| ID | Task | Size | Depends | Acceptance |
|---|---|---|---|---|
| P2c-1 | `TransactionDetailDrawer` + `ChargebackTimeline` (row click → `/transactions/{id}`) | M | P1d-3 | Shows payment, refunds, chargeback and ordered events |
| P2c-2 | Drill-down from the chargeback results table (via `transaction_id`) | S | P2c-1 | Works for both entity tables |
| P2c-3 | E2E for drill-down | S | P2c-2 | Passes |

### Phase 3 — Hardening (optional)
| ID | Task | Size | Acceptance |
|---|---|---|---|
| P3-1 | Expand the natural-language eval set to ~100 prompts; CI job for the offline subset | M | Pass rate tracked per commit |
| P3-2 | Structured JSON logs + request IDs across all services | S | One request can be traced across all services |
| P3-3 | Token and cost metrics per chat turn | S | Shown in logs/metrics |
| P3-4 | Swap `InMemorySessionStore` → `RedisSessionStore` | S | Sessions survive an agent restart |

### Critical path
`P0-1 → P0-4 → P1a-3 → P1a-6 → P1a-8 → P1a-10 → P1b-3 → P1c-2 → P1c-5 → P1c-7 → P1d-3 → P1d-4`

The UI scaffold (P1d-1/2) can be built in parallel against a mocked `/chat` once P1c-7's contract is fixed.
