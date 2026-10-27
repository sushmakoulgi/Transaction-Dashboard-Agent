from typing import Literal, Self

from pydantic import Field, model_validator

from shared.contracts.contract_model import ContractModel
from shared.contracts.filter_expression import FieldName, FilterNode, ensure_within_filter_limits

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


class SortSpec(ContractModel):
    field: FieldName
    direction: Literal["asc", "desc"] = "desc"


class SearchRequest(ContractModel):
    filter: FilterNode | None = None
    sort: SortSpec | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @model_validator(mode="after")
    def check_filter_limits(self) -> Self:
        if self.filter is not None:
            ensure_within_filter_limits(self.filter)
        return self
