import math
from typing import Any

from pydantic import Field, computed_field, model_validator

from shared.contracts.contract_model import ContractModel


class PaginatedResult[ItemT](ContractModel):
    items: list[ItemT]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_count: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pages(self) -> int:
        return math.ceil(self.total_count / self.page_size)

    @model_validator(mode="before")
    @classmethod
    def accept_matching_total_pages(cls, data: Any) -> Any:
        """Lets a serialized result (which includes `total_pages`) be read back."""
        if not isinstance(data, dict) or "total_pages" not in data:
            return data
        fields = dict(data)
        received_total_pages = fields.pop("total_pages")
        total_count, page_size = fields.get("total_count"), fields.get("page_size")
        if (
            isinstance(total_count, int)
            and isinstance(page_size, int)
            and page_size > 0
            and received_total_pages != math.ceil(total_count / page_size)
        ):
            raise ValueError(
                f"total_pages {received_total_pages} does not match "
                f"total_count {total_count} / page_size {page_size}"
            )
        return fields
