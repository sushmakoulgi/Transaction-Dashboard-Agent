"""Session memory in Amazon DynamoDB, shared by every agent instance (e.g. on AWS Lambda).

Same behaviour as `InMemorySessionStore`: each session keeps its last N entries (question +
query plan only) and expires after an idle period that every read or write restarts.

One item per session:

    {"sessionId": "...", "entries": ["<SessionEntry JSON>", ...], "ttl_session": <epoch s>,
     "version": <int>}

(Attribute names follow the AWS table `TransactionSessionStore`.)

- Reads are strongly consistent, so a follow-up question always sees the previous one.
- Appends are optimistic: write only if `version` is unchanged, else re-read and retry, so
  concurrent appends to one session never overwrite each other.
- `ttl_session` is the table's TTL attribute: DynamoDB deletes expired items by itself, but
  only eventually, so expiry is also checked here on every read.

boto3 is synchronous; its calls run in a worker thread to keep the event loop free.
"""

import asyncio
import random
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from agent_service.memory.session_store import SessionEntry, ensure_valid_session_id
from shared.clock import Clock

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

MAX_APPEND_ATTEMPTS = 20
KEY_ATTRIBUTE = "sessionId"  # the partition key (String)
TTL_ATTRIBUTE = "ttl_session"  # epoch seconds; the table's TTL attribute


class SessionStoreUnavailableError(RuntimeError):
    """DynamoDB could not be reached, refused the request, or a session stayed contended."""


class DynamoDbSessionStore:
    def __init__(
        self,
        client: "DynamoDBClient",
        table_name: str,
        max_entries_per_session: int,
        idle_timeout: timedelta,
        clock: Clock,
    ) -> None:
        if max_entries_per_session < 1:
            raise ValueError("max_entries_per_session must be at least 1")
        self._client = client
        self._table_name = table_name
        self._max_entries_per_session = max_entries_per_session
        self._idle_timeout = idle_timeout
        self._clock = clock

    async def load_recent_entries(self, session_id: str) -> list[SessionEntry]:
        ensure_valid_session_id(session_id)
        item = await self._get(session_id)
        if item is None or self._is_expired(item):
            return []
        await self._call(
            self._client.update_item,
            TableName=self._table_name,
            Key={KEY_ATTRIBUTE: {"S": session_id}},
            UpdateExpression="SET #expires = :expires",
            ConditionExpression="attribute_exists(#key)",
            ExpressionAttributeNames={"#key": KEY_ATTRIBUTE, "#expires": TTL_ATTRIBUTE},
            ExpressionAttributeValues={":expires": {"N": str(self._new_expiry())}},
            ignore_condition_failure=True,  # deleted meanwhile: nothing to refresh
        )
        return [SessionEntry.model_validate_json(value["S"]) for value in item["entries"]["L"]]

    async def append_entry(self, session_id: str, entry: SessionEntry) -> None:
        ensure_valid_session_id(session_id)
        if not isinstance(entry, SessionEntry):
            raise TypeError("only SessionEntry objects can be stored")
        for attempt in range(MAX_APPEND_ATTEMPTS):
            item = await self._get(session_id)
            # An expired item is overwritten (fresh session), still guarded by its version.
            kept = (
                [value["S"] for value in item["entries"]["L"]]
                if item is not None and not self._is_expired(item)
                else []
            )
            entries = [*kept, entry.model_dump_json()][-self._max_entries_per_session :]
            version = int(item["version"]["N"]) if item is not None else None
            written = await self._call(
                self._client.put_item,
                TableName=self._table_name,
                Item={
                    KEY_ATTRIBUTE: {"S": session_id},
                    "entries": {"L": [{"S": value} for value in entries]},
                    TTL_ATTRIBUTE: {"N": str(self._new_expiry())},
                    "version": {"N": str((version or 0) + 1)},
                },
                **(
                    {
                        "ConditionExpression": "attribute_not_exists(#key)",
                        "ExpressionAttributeNames": {"#key": KEY_ATTRIBUTE},
                    }
                    if version is None
                    else {
                        "ConditionExpression": "version = :version",
                        "ExpressionAttributeValues": {":version": {"N": str(version)}},
                    }
                ),
                ignore_condition_failure=True,
            )
            if written:
                return
            await asyncio.sleep(random.uniform(0, 0.02 * (attempt + 1)))  # back off, then retry
        raise SessionStoreUnavailableError(
            "the session is being updated by too many requests at once; try again"
        )

    async def delete_session(self, session_id: str) -> None:
        ensure_valid_session_id(session_id)
        await self._call(
            self._client.delete_item,
            TableName=self._table_name,
            Key={KEY_ATTRIBUTE: {"S": session_id}},
        )

    async def _get(self, session_id: str) -> dict[str, Any] | None:
        response = await self._call(
            self._client.get_item,
            TableName=self._table_name,
            Key={KEY_ATTRIBUTE: {"S": session_id}},
            ConsistentRead=True,
        )
        item: dict[str, Any] | None = response.get("Item")
        return item

    def _is_expired(self, item: dict[str, Any]) -> bool:
        return int(item[TTL_ATTRIBUTE]["N"]) <= self._clock.now().timestamp()

    def _new_expiry(self) -> int:
        return int((self._clock.now() + self._idle_timeout).timestamp())

    async def _call(
        self, operation: Any, ignore_condition_failure: bool = False, **request: Any
    ) -> Any:
        """Runs one DynamoDB call in a thread. A failed condition returns None when ignored;
        any other failure becomes `SessionStoreUnavailableError` (no request data in it)."""
        try:
            return await asyncio.to_thread(operation, **request)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if ignore_condition_failure and code == "ConditionalCheckFailedException":
                return None
            raise SessionStoreUnavailableError(f"session store request failed ({code})") from error
        except BotoCoreError as error:
            raise SessionStoreUnavailableError(
                f"session store unreachable ({type(error).__name__})"
            ) from error


def create_session_table(client: "DynamoDBClient", table_name: str) -> None:
    """Creates the table (on-demand, TTL on `ttl_session`) if it does not exist. For DynamoDB
    Local and tests; in AWS the table is created once with the same settings."""
    existing = client.list_tables()["TableNames"]
    if table_name not in existing:
        client.create_table(
            TableName=table_name,
            AttributeDefinitions=[{"AttributeName": KEY_ATTRIBUTE, "AttributeType": "S"}],
            KeySchema=[{"AttributeName": KEY_ATTRIBUTE, "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=table_name)
    ttl = client.describe_time_to_live(TableName=table_name)["TimeToLiveDescription"]
    if ttl.get("TimeToLiveStatus") not in ("ENABLED", "ENABLING"):
        client.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
        )
