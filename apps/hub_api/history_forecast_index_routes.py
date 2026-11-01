"""Signed-owner metadata index; never reads originals or produces forecasts."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from packages.contracts_py.decision_hub_contracts.generated.history_forecast_index_v2 import (
    HistoryForecastIndexV2,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope

from .history_owner_authority import HISTORY_COOKIE
from .history_product_routes import HistoryProductBootstrap
from .local_owner_session import LocalOwnerAuthenticationError

ASSETS = tuple(
    (asset, spot_scope(asset + "-USDT").scope_id, label)
    for asset, label in (("BTC", "比特币"), ("ETH", "以太坊"))
)
MODEL_ID = re.compile(r"^hfit2_[a-f0-9]{48}$")


class HistoryForecastIndexV2Reader:
    """Two owner-indexed small-header SELECTs at most; no payload parsing."""

    def __init__(self, bootstrap):
        if type(bootstrap) is not HistoryProductBootstrap:
            raise ValueError("history_forecast_index_actual_bootstrap_required")
        self.bootstrap = bootstrap
        self.owner = bootstrap.authority.owner_identity
        self.originals = bootstrap.originals
        self.commands = self.originals.commands
        self.forecasts = bootstrap.forecasts
        self.grants = () if self.forecasts is None else tuple(sorted(self.forecasts.grants.items()))

    def read(self):
        boot = self.bootstrap
        if (
            boot.authority.owner_identity != self.owner
            or boot.originals is not self.originals
            or self.originals.commands is not self.commands
            or boot.forecasts is not self.forecasts
            or (() if self.forecasts is None else tuple(sorted(self.forecasts.grants.items())))
            != self.grants
        ):
            raise ValueError("history_forecast_index_trusted_binding_changed")
        items = []
        with self.commands._read_session() as session:
            observed = self.originals.now()
            for asset, scope, label in ASSETS:
                model = dict(self.grants).get(scope)
                item = dict(
                    asset=asset,
                    scope_id=scope,
                    asset_label=label,
                    model_id=model,
                    state="not_granted" if model is None else "not_generated",
                    forecast_id=None,
                    cutoff_at=None,
                    recorded_at=None,
                    probabilities=None,
                    reason_label="服务器尚未授权此资产的模型。"
                    if model is None
                    else "当前授权模型尚无已保存预测。",
                )
                if model is not None:
                    if MODEL_ID.fullmatch(model) is None:
                        raise ValueError("history_forecast_index_grant_identity_invalid")
                    row = (
                        session.execute(
                            select(
                                Forecast.forecast_id,
                                Forecast.owner_claim,
                                Forecast.request_id,
                                Forecast.scope_id,
                                Forecast.model_id,
                                Forecast.cutoff_at,
                                Forecast.recorded_at,
                            )
                            .where(
                                Forecast.owner_claim == self.owner,
                                Forecast.scope_id == scope,
                                Forecast.model_id == model,
                            )
                            .order_by(
                                Forecast.recorded_at.desc(),
                                Forecast.cutoff_at.desc(),
                                Forecast.forecast_id.desc(),
                            )
                            .limit(1)
                        )
                        .mappings()
                        .first()
                    )
                    if row is not None:
                        if (row["owner_claim"], row["scope_id"], row["model_id"]) != (
                            self.owner,
                            scope,
                            model,
                        ) or row["forecast_id"] != "hforecast2_" + canonical_hash(
                            [self.owner, row["request_id"]]
                        )[:48]:
                            raise ValueError("history_forecast_index_saved_identity_invalid")
                        item.update(
                            state="verification_required",
                            forecast_id=row["forecast_id"],
                            cutoff_at=as_utc(row["cutoff_at"]),
                            recorded_at=as_utc(row["recorded_at"]),
                            reason_label="发现已保存预测；读取完整原件核验后才能展示频率。",
                        )
                items.append(item)
            value = HistoryForecastIndexV2.model_validate_json(
                json.dumps(
                    dict(
                        schema_version="history-forecast-index.v2",
                        owner_authenticated=True,
                        observed_at=observed,
                        current_originals_verified=False,
                        financial_qualified=False,
                        historical_pit_verified=False,
                        scopes=items,
                    ),
                    default=lambda value: value.isoformat(),
                )
            )
        return value


def history_forecast_index_router(bootstrap=None):
    router = APIRouter()
    index = None if bootstrap is None else HistoryForecastIndexV2Reader(bootstrap)

    @router.get("/v1/history/forecast-index", response_model=HistoryForecastIndexV2)
    def forecast_index(request: Request):
        if index is None:
            raise HTTPException(503, "history_owner_not_configured")
        try:
            bootstrap.authority.resolve(
                request.cookies.get(HISTORY_COOKIE, ""),
                origin=request.headers.get("Origin"),
                mutation=False,
            )
        except LocalOwnerAuthenticationError:
            raise HTTPException(403, "history_owner_session_rejected") from None
        try:
            return index.read()
        except (ValueError, TypeError, KeyError, OSError, SQLAlchemyError):
            raise HTTPException(409, "history_forecast_index_metadata_unavailable") from None

    return router
