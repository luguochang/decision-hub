"""Release39 executor in the unchanged private launch configuration shape."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch39 import (
    HistoryForecastDispatch39Service,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_write39 import (
    HistoryForecastWrite39Service,
)

from .history_product_bootstrap import configured_history_product

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def configured_history_product39(database):
    # Existing config validates server owner/origin/pairing/CSRF/resource grants.
    legacy = configured_history_product(database)
    if legacy is None or legacy.forecasts is None:
        return legacy
    writer = HistoryForecastWrite39Service(
        legacy.forecasts.models,
        owner_claim=legacy.forecasts.owner_claim,
        grants=dict(legacy.forecasts.grants),
    )
    return replace(legacy, forecasts=HistoryForecastDispatch39Service(writer))
