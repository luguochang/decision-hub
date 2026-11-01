from pathlib import Path

import jsonschema
import pytest
import yaml

from packages.contracts_py.decision_hub_contracts.generated.market_risk_view import (
    RiskTimelinePoint,
)


@pytest.mark.parametrize("price", ["1", "44000.1", "0.00001"])
def test_canonical_and_python_decimal_price_acceptance(price: str) -> None:
    source = Path(__file__).resolve().parents[2] / "contracts/schemas/market_risk_view.schema.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    schema = {**document, "$ref": "#/$defs/risk_timeline_point"}
    payload = {"at": "2026-10-08T00:00:00Z", "price": price}
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(
        payload
    )
    assert RiskTimelinePoint.model_validate(payload).price == price


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "1e3", ".1"])
def test_canonical_and_python_decimal_price_rejection(price: str) -> None:
    source = Path(__file__).resolve().parents[2] / "contracts/schemas/market_risk_view.schema.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    schema = {**document, "$ref": "#/$defs/risk_timeline_point"}
    payload = {"at": "2026-10-08T00:00:00Z", "price": price}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(payload)
    with pytest.raises(ValueError):
        RiskTimelinePoint.model_validate(payload)
