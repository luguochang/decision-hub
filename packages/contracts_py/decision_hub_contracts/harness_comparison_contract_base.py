from pydantic import BaseModel, ConfigDict

class HarnessComparisonContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
