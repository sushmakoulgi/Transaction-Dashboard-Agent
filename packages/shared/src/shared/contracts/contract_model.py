from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base for data exchanged between services: rejects unknown keys and is immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
