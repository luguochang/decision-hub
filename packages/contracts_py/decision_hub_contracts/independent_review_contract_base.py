"""Validate the canonical reviewer wire shape; parsing conveys no authority."""

from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, model_validator


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    path = Path(__file__).resolve().parents[3] / "contracts/schemas/independent_review.schema.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        definition["title"]: Draft202012Validator(
            {"$defs": document["$defs"], "$ref": f"#/$defs/{name}"}
        )
        for name, definition in document["$defs"].items()
    }


def _wire(value: object) -> object:
    if isinstance(value, BaseModel):
        return _wire(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


class IndependentReviewContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def canonical_shape(cls, value: object) -> object:
        validator = _validators().get(cls.__name__)
        if validator is None:
            raise ValueError("independent_review_canonical_definition_required")
        error = next(validator.iter_errors(_wire(value)), None)
        if error is not None:
            path = ".".join(str(item) for item in error.absolute_path)
            raise ValueError(f"independent_review_shape_invalid:{error.validator}:{path}")
        return value
