"""Private paper cashflow observations; neither probability labels nor skill authority."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from .exact_decimal_text import number
from .market_path import canonical_hash

SCHEMA = "perpetual-paper-episode-experience.v1"
ROLE = "perpetual_paper_episode_experience_v1:"
KIND = "paper_episode_net_cashflow"
SOURCE = "perpetual_paper_episode_experience.v1"
METHOD = dict(
    version="paper-episode-realized-cashflow.v1",
    net="trade_cash_delta+funding_cash",
    cash="gross-trade_fees",
    funding="complete_zero_settlements_only",
    slippage="already_in_fill_price",
    probability_scored=False,
)
_METHOD_V1_HASH = canonical_hash(METHOD)
SOFTWARE_FILES = (
    "application/perpetual_paper_experience.py",
    "ports/perpetual_paper_experience.py",
    "application/perpetual_funding_history_source.py",
)


def validate_calculation_method(method, method_hash):
    """Only the supported semantic method authorizes financial replay."""
    if not isinstance(method, dict) or method_hash != _METHOD_V1_HASH:
        raise ValueError("paper_experience_method_unsupported")
    try:
        actual = canonical_hash(method)
    except (TypeError, ValueError) as error:
        raise ValueError("paper_experience_method_unsupported") from error
    if actual != _METHOD_V1_HASH:
        raise ValueError("paper_experience_method_unsupported")


def validate_creation_software(value):
    """Preserve hash-bound creation metadata without loading its old code."""
    if (
        not isinstance(value, dict)
        or set(value) != set(SOFTWARE_FILES)
        or any(
            not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            for digest in value.values()
        )
    ):
        raise ValueError("paper_experience_creation_software_invalid")
    return dict(value)


def cashflow_label(gross, fees, cash, funding):
    values = tuple(Decimal(value) for value in (gross, fees, cash, funding))
    if any(not value.is_finite() for value in values) or values[1] < 0:
        raise ValueError("paper_experience_nonfinite_or_negative_fee")
    with localcontext() as ctx:
        ctx.prec = 50
        if values[0] - values[1] != values[2]:
            raise ValueError("paper_experience_cashflow_identity_invalid")
        net = values[2] + values[3]
        return number(net), "gain" if net > 0 else "loss" if net < 0 else "flat"


@dataclass(frozen=True)
class PaperEpisodeExperienceOriginal:
    ref: DecisionSourceRefV1
    body_json: str

    @property
    def body(self):
        return json.loads(self.body_json)


def experience_original(body):
    key = canonical_hash([body["case_id"], body["source_version_key"]])
    return PaperEpisodeExperienceOriginal(
        DecisionSourceRefV1(ref="paper_episode_experience_" + key, hash=canonical_hash(body)),
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
    )
