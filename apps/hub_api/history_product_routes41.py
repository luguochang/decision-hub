"""Signed-owner product API using explicitly granted frozen qualifications.

The ordinary status route reads headers through the legacy metadata reader.
Only an explicit refresh or original read invokes the qualified executor.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from packages.contracts_py.decision_hub_contracts.generated.history_product_status_v2 import (
    HistoryAssetRefreshReplyV2,
    HistoryOwnerSessionPairV2,
    HistoryOwnerSessionReplyV2,
    HistoryProductStatusV2,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    HistoryDatasetForecastCommittedReadbackError,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as ForecastRow,
)

from .history_owner_authority import HISTORY_COOKIE, HISTORY_COOKIE_PATH
from .history_product_bootstrap41 import HistoryProductBootstrap41
from .history_product_routes import ASSETS, HistoryFastStatus, _verified_reply
from .local_owner_session import LocalOwnerAuthenticationError

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _unconfirmed_reply(asset, originals, error=None):
    known = isinstance(error, HistoryDatasetForecastCommittedReadbackError)
    return HistoryAssetRefreshReplyV2.model_validate_json(
        json.dumps(
            dict(
                schema_version="history-asset-refresh-reply.v2",
                owner_authenticated=True,
                asset=asset,
                outcome="committed_readback_unconfirmed" if known else "unconfirmed",
                committed=True if known else None,
                automatic_retry=False,
                forecast_id=error.forecast_id if known else None,
                cutoff_at=error.cutoff_at.isoformat() if known else None,
                horizon_seconds=86400,
                target="24h_path_mae",
                financial_qualified=False,
                historical_pit_verified=False,
                fixture_mode=originals.fixture_mode,
                frequencies=[],
                user_message="保存已提交，独立读回尚未确认；不会自动重复执行。"
                if known
                else "资格、分钟原件或提交回执尚未确认；不会自动重复执行。",
            )
        )
    )


def history_product_router41(bootstrap: HistoryProductBootstrap41 | None = None):
    if bootstrap is not None and type(bootstrap) is not HistoryProductBootstrap41:
        raise ValueError("history41_actual_product_bootstrap_required")
    if bootstrap is not None:
        bootstrap.__post_init__()
    reader = None if bootstrap is None else HistoryFastStatus(bootstrap.legacy)
    router = APIRouter(prefix="/v1/history", tags=["history"])

    def configured():
        if bootstrap is None:
            raise HTTPException(503, detail="history_owner_not_configured")
        try:
            bootstrap.__post_init__()
            if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256:
                raise ValueError("history41_product_loaded_source_changed")
        except (ValueError, OSError):
            raise HTTPException(409, detail="history41_configuration_changed") from None
        return bootstrap

    def authenticated(request, mutation=False):
        boot = configured()
        try:
            boot.authority.resolve(
                request.cookies.get(HISTORY_COOKIE, ""),
                origin=request.headers.get("origin"),
                mutation=mutation,
                csrf_token=request.headers.get("X-History-CSRF"),
            )
        except LocalOwnerAuthenticationError:
            raise HTTPException(403, detail="history_owner_session_rejected") from None
        return boot

    @router.post("/owner-session", response_model=HistoryOwnerSessionReplyV2)
    def pair(body: HistoryOwnerSessionPairV2, request: Request, response: Response):
        boot = configured()
        try:
            session = boot.authority.pair(
                body.pairing_token, origin=request.headers.get("origin", "")
            )
        except LocalOwnerAuthenticationError:
            raise HTTPException(403, detail="history_owner_pairing_rejected") from None
        response.set_cookie(
            HISTORY_COOKIE,
            session.cookie_value,
            httponly=True,
            samesite="strict",
            path=HISTORY_COOKIE_PATH,
            max_age=boot.authority._authority.lifetime_seconds,
        )
        return HistoryOwnerSessionReplyV2(
            csrf_token=session.csrf_token, expires_at=session.expires_at
        )

    @router.get("/status", response_model=HistoryProductStatusV2)
    def status(
        request: Request,
        job_offset: int = Query(0, ge=0, le=1199),
        job_limit: int = Query(30, ge=1, le=60),
        excluded_offset: int = Query(0, ge=0, le=179),
        excluded_limit: int = Query(30, ge=1, le=60),
    ):
        boot = authenticated(request)
        try:
            payload = reader.status(
                job_offset=job_offset,
                job_limit=job_limit,
                excluded_offset=excluded_offset,
                excluded_limit=excluded_limit,
            ).model_dump(mode="json")
            payload["actions"]["can_refresh"] = boot.allow_producer and bool(boot.serving_grants)
            payload["forecast"]["blocked_reason_label"] = (
                "显式刷新使用服务器授予的冻结模型资格和最新31条已收盘分钟原件"
            )
            for model in payload["models"]:
                if model["model_id"] is not None:
                    model["blocked_reason_label"] = (
                        "模型已保存；当前快状态不认证原件，显式刷新将核验已确认资格"
                    )
            return HistoryProductStatusV2.model_validate_json(json.dumps(payload))
        except (ValueError, OSError, SQLAlchemyError, KeyError, TypeError):
            raise HTTPException(409, detail="history_status_saved_metadata_unavailable") from None

    @router.post("/assets/{asset}/refresh", response_model=HistoryAssetRefreshReplyV2)
    def refresh(asset: Literal["BTC", "ETH"], request: Request):
        boot = authenticated(request, True)
        scope = ASSETS[asset]
        if not boot.allow_producer or scope not in dict(boot.serving_grants):
            raise HTTPException(403, detail="history_producer_scope_not_granted")
        # One call only. Even a lost acknowledgement never starts a second producer.
        try:
            value = boot.forecasts.producer_once(scope)
        except HistoryDatasetForecastCommittedReadbackError as error:
            return _unconfirmed_reply(asset, boot.originals, error)
        except (ValueError, OSError, SQLAlchemyError):
            return _unconfirmed_reply(asset, boot.originals)
        return _verified_reply(asset, value)

    @router.get("/forecasts/{forecast_id}", response_model=HistoryAssetRefreshReplyV2)
    def original_forecast(forecast_id: str, request: Request):
        boot = authenticated(request)
        if re.fullmatch(r"hforecast2_[a-f0-9]{48}", forecast_id) is None:
            raise HTTPException(403, detail="history_forecast_read_not_granted")
        try:
            with boot.originals.commands._read_session() as session:
                row = session.get(ForecastRow, forecast_id)
                if (
                    row is None
                    or row.owner_claim != boot.authority.owner_identity
                    or boot.forecasts.grants.get(row.scope_id) != row.model_id
                ):
                    raise HTTPException(403, detail="history_forecast_read_not_granted")
                value = boot.forecasts.read(session, forecast_id)
            asset = next(asset for asset, scope in ASSETS.items() if scope == value.scope_id)
            return _verified_reply(asset, value)
        except (ValueError, OSError, SQLAlchemyError):
            raise HTTPException(
                409, detail="history_forecast_original_verification_failed"
            ) from None

    return router
