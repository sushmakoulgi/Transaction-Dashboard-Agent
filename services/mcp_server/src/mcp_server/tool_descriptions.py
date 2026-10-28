"""What the LLM reads about each tool. Precise wording here directly improves tool use."""

FILTER_LANGUAGE = """
FILTER LANGUAGE (never SQL). A filter is a tree of:
- condition: {"kind": "condition", "field": <name>, "operator": <op>, "value": <value>}
- group: {"kind": "group", "logic": "and" | "or", "negate": false | true, "children": [...]}
  "negate": true means NOT (...). Groups nest at most 3 deep; at most 20 conditions in total.

OPERATORS (each field allows a subset; see list_filterable_fields):
eq, neq, gt, gte, lt, lte, in, not_in (value is a list), between (value is [start, end]),
contains (case-insensitive text match), is_null (value true/false),
exists (value true/false; for has_refund / has_chargeback).

DATES (America/New_York business time):
- A date "2026-10-15" means that whole day; "gt 2026-10-15" means after that day.
- Prefer relative dates with between: {"relative": "today" | "yesterday" | "this_month" |
  "last_month" | "this_year"} or {"last_n_days": 7}.
- Only the current calendar year can be searched. Dates outside it are rejected, and every
  search is limited to the current year automatically.

SEMANTICS:
- AND-ed conditions on refunds (refund_status, refunded_at) or chargeback events
  (chargeback_event_type, chargeback_event_date) must all match the SAME refund / event.
- Negation (neq, not_in, negate) includes rows where the field is empty, e.g. NOT card_brand
  = AMEX includes bank (ACH) payments.

If a field name or value is rejected, the error lists exact problems with suggestions:
fix them and call again. Call list_filterable_fields when unsure which fields exist.
""".strip()

PAGING = (
    "Results are paginated: page (from 1) and page_size (1-100, default 25). "
    'sort is {"field": <sortable field>, "direction": "asc" | "desc"}; '
    "default is newest first."
)

SEARCH_TRANSACTIONS = f"""
Search payment transactions. Each row includes the payment (status, method, card brand),
whether it has refunds or a chargeback, and the chargeback status.

Use for: transactions on a date, for a customer_id, for a payment_id, with chargebacks this
month (chargeback_opened_at between this_month), with refunds, by amount or merchant.

Example, a customer's transactions this month:
{{"filter": {{"kind": "group", "logic": "and", "children": [
  {{"kind": "condition", "field": "customer_id", "operator": "eq", "value": "C-1004"}},
  {{"kind": "condition", "field": "transaction_date", "operator": "between",
   "value": {{"relative": "this_month"}}}}]}}}}

{PAGING}

{FILTER_LANGUAGE}
""".strip()

SEARCH_CHARGEBACKS = f"""
Search chargebacks (disputes). Each row includes the reason code, status, amount, network,
latest event type and the disputed transaction's date.

Use for: chargebacks for a customer, chargebacks opened this month (opened_at between
this_month), by status, by reason code, or with a given event (chargeback_event_type).

Example, a customer's chargebacks opened this month:
{{"filter": {{"kind": "group", "logic": "and", "children": [
  {{"kind": "condition", "field": "customer_id", "operator": "eq", "value": "C-1004"}},
  {{"kind": "condition", "field": "opened_at", "operator": "between",
   "value": {{"relative": "this_month"}}}}]}}}}

{PAGING}

{FILTER_LANGUAGE}
""".strip()

GET_TRANSACTION_DETAIL = """
Everything about one transaction: merchant and metadata, the payment (authorization and
capture), all refunds (oldest first), and the chargeback with its event timeline in order.
Use after a search, with a transaction_id from the results. Transactions outside the current
year are reported as not found.
""".strip()

LIST_FILTERABLE_FIELDS = """
The fields a search can filter or sort on for an entity ("transactions" or "chargebacks"):
each field's type, allowed operators, allowed values (for enums) and whether it is sortable.
Call this before building a filter when you are unsure of a field name or value.
""".strip()

VALIDATE_FILTERS = f"""
Check a filter (and optional sort / paging) for an entity without running the search.
Returns is_valid and, if invalid, every problem with its path and suggestions. Useful for
checking a complex filter before searching.

{FILTER_LANGUAGE}
""".strip()
