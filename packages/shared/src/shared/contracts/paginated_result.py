import math
from typing import Any

from pydantic import Field, model_validator

from shared.contracts.contract_model import ContractModel


class PaginatedResult[ItemT](ContractModel):
    """One page of results. `total_pages` is always derived from `total_count` / `page_size`.

    It is a real field (not a computed property) so the JSON schema matches the serialized
    data, e.g. for MCP tools that validate structured output against their output schema.
    """

    items: list[ItemT]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_count: int = Field(ge=0)
    total_pages: int = Field(default=0, ge=0)  # always replaced by derive_total_pages

    @model_validator(mode="before")
    @classmethod
    def derive_total_pages(cls, data: Any) -> Any:
        """Fills in `total_pages`, rejecting a supplied value that doesn't match."""
        if not isinstance(data, dict):
            return data
        total_count, page_size = data.get("total_count"), data.get("page_size")
        if not (isinstance(total_count, int) and isinstance(page_size, int) and page_size > 0):
            return data  # the field validators report the bad input
        expected_total_pages = math.ceil(total_count / page_size)
        supplied = data.get("total_pages", expected_total_pages)
        if supplied != expected_total_pages:
            raise ValueError(
                f"total_pages {supplied} does not match "
                f"total_count {total_count} / page_size {page_size}"
            )
        return {**data, "total_pages": expected_total_pages}
