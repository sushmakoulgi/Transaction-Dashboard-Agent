from enum import StrEnum

from shared.contracts.contract_model import ContractModel
from shared.contracts.filter_expression import Operator


class SearchableEntity(StrEnum):
    TRANSACTIONS = "transactions"
    CHARGEBACKS = "chargebacks"


class FilterableFieldDescription(ContractModel):
    """What the LLM sees about one filterable field (via `list_filterable_fields`)."""

    name: str
    description: str
    field_type: str
    operators: tuple[Operator, ...]
    allowed_values: tuple[str, ...] = ()
    is_sortable: bool = False
