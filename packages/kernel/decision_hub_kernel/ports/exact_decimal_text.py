"""Canonical plain decimal text shared by transport, policy and persistence."""
from decimal import Decimal


def number(value: Decimal) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("perp_paper_finite_decimal_required")
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
