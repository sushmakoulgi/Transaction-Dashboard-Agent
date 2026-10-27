import math

from pydantic import Field, computed_field

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
