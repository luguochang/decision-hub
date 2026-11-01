"""Bounded history metadata and explicit authenticated future production.

No launcher configuration means unavailable. Cookie proof establishes the
owner; server grants establish resources. Fast status never runs a full reader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from packages.contracts_py.decision_hub_contracts.generated.history_product_status_v2 import (
    HistoryAssetRefreshReplyV2,
    HistoryOwnerSessionPairV2,
    HistoryOwnerSessionReplyV2,
    HistoryProductStatusV2,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    HistoryDatasetForecastCommittedReadbackError,
    HistoryDatasetForecastV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch39 import (
    HistoryForecastDispatch39Service,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import verify_payload
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as ForecastRow,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record as ModelRow,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryDatasetV2OriginalRecord as Original,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2EpochRecord as Epoch,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2JobRecord as Job,
)
from packages.kernel.decision_hub_kernel.ports.history_dataset_model_v2 import (
    HistoryDatasetFitModelV2,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope

from .history_owner_authority import HISTORY_COOKIE, HISTORY_COOKIE_PATH, HistoryOwnerAuthority
from .local_owner_session import LocalOwnerAuthenticationError

ASSETS = {asset: spot_scope(asset + "-USDT").scope_id for asset in ("BTC", "ETH")}
LABELS = {ASSETS["BTC"]: "比特币", ASSETS["ETH"]: "以太坊"}


def _verified_reply(asset, value):
    return HistoryAssetRefreshReplyV2.model_validate_json(
        json.dumps(
            dict(
                schema_version="history-asset-refresh-reply.v2",
                owner_authenticated=True,
                asset=asset,
                outcome="committed_verified",
                committed=True,
                automatic_retry=False,
                forecast_id=value.forecast_id,
                cutoff_at=value.cutoff_at.isoformat(),
                horizon_seconds=value.horizon_seconds,
                target=value.target,
                financial_qualified=False,
                historical_pit_verified=False,
                fixture_mode=value.fixture_mode,
                frequencies=[
                    dict(
                        threshold=item.threshold,
                        baseline_n=item.baseline_n,
                        conditional_n=item.conditional_n,
                        probabilities=dict(
                            baseline_long=str(item.baseline_long_probability),
                            baseline_short=str(item.baseline_short_probability),
                            conditional_long=str(item.conditional_long_probability),
                            conditional_short=str(item.conditional_short_probability),
                        ),
                    )
                    for item in value.frequencies
                ],
                user_message="已保存并重新读取未来24小时路径逆向损失频率估计；尚未获得金融交付资格。",
            )
        )
    )


@dataclass(frozen=True)
class HistoryProductBootstrap:
    """Only a trusted server composition may create this; never deserialize HTTP input."""

    authority: HistoryOwnerAuthority
    originals: HistoryDatasetV2Service
    plan_id: str | None = None
    dataset_id: str | None = None
    epoch_id: str | None = None
    forecasts: HistoryDatasetForecastV2Service | HistoryForecastDispatch39Service | None = None
    allow_producer: bool = False

    def __post_init__(self):
        if type(self.authority) is not HistoryOwnerAuthority or (
            type(self.originals) is not HistoryDatasetV2Service
        ):
            raise ValueError("history_private_bootstrap_dependency_invalid")
        if self.forecasts is not None and (
            type(self.forecasts)
            not in (HistoryDatasetForecastV2Service, HistoryForecastDispatch39Service)
            or self.forecasts.owner_claim != self.authority.owner_identity
            or self.forecasts.models.originals is not self.originals
        ):
            raise ValueError("history_private_producer_owner_or_resource_invalid")
        if type(self.allow_producer) is not bool or self.allow_producer and self.forecasts is None:
            raise ValueError("history_private_producer_grant_invalid")


def _reason(code):
    return {
        "missing_or_unowned_first_known_original": "分钟数据缺失，或无法证明首次获知的原件归属",
        "market_path_transport_unavailable": "数据源请求失败，已保留失败记录",
        "history_v2_transport_circuit_open": "连续数据源故障，采集已暂停",
        "missing_closed_original": "完整已收盘分钟数据不足",
        "source_not_known_by_dataset_K": "数据在冻结时点尚不可知",
    }.get(code, "该窗口尚未满足完整原件与时点核验条件")


class HistoryFastStatus:
    """Small hash-keyed projection; no archive/raw reads and no mathematical fit."""

    def __init__(self, bootstrap):
        self.bootstrap = bootstrap
        self.cache = {}

    def _original(self, session, identifier, kind):
        if not identifier:
            return None
        header = session.execute(
            select(
                Original.payload_hash,
                Original.owner_claim,
                Original.kind,
            ).where(Original.original_id == identifier)
        ).one_or_none()
        if header is None or (header.owner_claim, header.kind) != (
            self.bootstrap.authority.owner_identity,
            kind,
        ):
            raise ValueError("history_status_granted_original_missing_or_owner_conflict")
        key = identifier, header.payload_hash
        if key not in self.cache:
            fields = {
                "plan": ("missing_intervals",),
                "dataset": ("windows",),
                "epoch": ("budget", "deadline_at"),
            }[kind]
            names = (
                "original_id",
                "plan_id",
                "owner_claim",
                "kind",
                "payload_hash",
                "known_asof",
                "cutoffs",
            )
            pairs = [(name, "$." + name) for name in names] + [
                (name, "$.provenance." + name) for name in fields
            ]
            columns = [
                part
                for name, path in pairs
                for part in (name, func.json_extract(Original.payload_json, path))
            ]
            body = session.scalar(
                select(func.json_object(*columns)).where(
                    Original.original_id == identifier,
                )
            )
            if len(body.encode()) > 1_048_576:
                raise ValueError("history_status_projection_size_exceeded")
            metadata = json.loads(body)
            if (
                metadata["payload_hash"],
                metadata["original_id"],
                metadata["owner_claim"],
                metadata["kind"],
            ) != (
                header.payload_hash,
                identifier,
                header.owner_claim,
                header.kind,
            ):
                raise ValueError("history_status_saved_metadata_header_changed")
            cutoffs = tuple(datetime.fromisoformat(value) for value in metadata["cutoffs"])
            if (
                len(cutoffs) != 90
                or any(value.utcoffset() is None or value.timestamp() % 86400 for value in cutoffs)
                or any(
                    right - left != timedelta(days=1)
                    for left, right in zip(cutoffs, cutoffs[1:], strict=False)
                )
            ):
                raise ValueError("history_status_fixed_cutoff_metadata_changed")
            if len(self.cache) >= 4:
                self.cache.clear()
            # Only metadata is projected. This deliberately makes no full payload,
            # archive or readset hash claim; actual operations use strict readers.
            self.cache[key] = SimpleNamespace(
                original_id=metadata["original_id"],
                plan_id=metadata["plan_id"],
                payload_hash=metadata["payload_hash"],
                known_asof=datetime.fromisoformat(metadata["known_asof"]),
                cutoffs=cutoffs,
                provenance={name: metadata[name] for name in fields},
            )
        return self.cache[key]

    def status(self, *, job_offset, job_limit, excluded_offset, excluded_limit):
        boot = self.bootstrap
        now = datetime.now(UTC)
        with boot.originals.commands._read_session() as session:
            plan = self._original(session, boot.plan_id, "plan")
            dataset = self._original(session, boot.dataset_id, "dataset")
            if dataset is not None and (plan is None or dataset.plan_id != plan.plan_id):
                raise ValueError("history_status_dataset_plan_grant_mismatch")
            excluded = []
            scopes = []
            for scope, label in LABELS.items():
                raw_count = first_count = missing_count = excluded_count = None
                quality = "unavailable"
                if plan is not None:
                    quality = "saved_metadata"
                    raw_count = 0
                    intervals = [
                        item
                        for item in plan.provenance["missing_intervals"]
                        if item["scope_id"] == scope
                    ]
                    for cutoff in plan.cutoffs:
                        # The 31 feature closes and next 1440 label minutes are disjoint.
                        segments = (
                            (cutoff - timedelta(minutes=31), cutoff),
                            (cutoff + timedelta(minutes=1), cutoff + timedelta(days=1, minutes=1)),
                        )
                        missing = sum(
                            max(
                                0,
                                int(
                                    (
                                        min(right, datetime.fromisoformat(item["end_at"]))
                                        - max(left, datetime.fromisoformat(item["start_at"]))
                                    ).total_seconds()
                                    // 60
                                ),
                            )
                            for left, right in segments
                            for item in intervals
                        )
                        if missing == 0:
                            raw_count += 1
                        elif dataset is None:
                            excluded.append(
                                dict(
                                    scope_id=scope,
                                    cutoff_at=cutoff,
                                    reason_code="missing_closed_original",
                                    reason_label=_reason("missing_closed_original"),
                                    missing_minutes=min(1471, missing),
                                )
                            )
                    missing_count = 90 - raw_count
                if dataset is not None:
                    windows = [
                        item for item in dataset.provenance["windows"] if item["scope_id"] == scope
                    ]
                    if len(windows) != 90:
                        raise ValueError("history_status_window_metadata_incomplete")
                    first_count = sum(item["status"] == "complete" for item in windows)
                    excluded_count = 90 - first_count
                    for item in windows:
                        if item["status"] == "excluded":
                            code = item["reason"] or "missing_or_unowned_first_known_original"
                            excluded.append(
                                dict(
                                    scope_id=scope,
                                    cutoff_at=item["cutoff_at"],
                                    reason_code=code,
                                    reason_label=_reason(code),
                                    missing_minutes=item["missing_minutes"],
                                )
                            )
                scopes.append(
                    dict(
                        scope_id=scope,
                        asset_label=label,
                        raw_complete_days=raw_count,
                        first_known_complete_days=first_count,
                        missing_days=missing_count,
                        excluded_days=excluded_count,
                        quality=quality,
                    )
                )
            models = []
            grants = {} if boot.forecasts is None else boot.forecasts.grants
            for scope in LABELS:
                identifier = grants.get(scope)
                counts = (None, None, None)
                model_hash = model_known = None
                state, label = "not_fitted", "尚无服务器授予的模型"
                if identifier:
                    row = session.get(ModelRow, identifier)
                    if (
                        row is None
                        or row.owner_claim != boot.authority.owner_identity
                        or row.scope_id != scope
                    ):
                        raise ValueError("history_status_model_grant_owner_mismatch")
                    value = verify_payload(
                        HistoryDatasetFitModelV2.model_validate_json(row.payload_json)
                    )
                    if value.payload_hash != row.payload_hash or value.model_id != identifier:
                        raise ValueError("history_status_saved_model_metadata_changed")
                    if (value.owner_claim, value.scope_id, value.plan_id, value.dataset_id) != (
                        row.owner_claim,
                        row.scope_id,
                        row.plan_id,
                        row.dataset_id,
                    ) or (boot.dataset_id is not None and value.dataset_id != boot.dataset_id):
                        raise ValueError("history_status_saved_model_resource_grant_changed")
                    counts = (value.baseline.n, value.conditional_low.n, value.conditional_high.n)
                    model_hash, model_known = value.payload_hash, value.model_known_at
                    state, label = (
                        "verification_required",
                        "模型已保存；生成未来结果时将重新核验全部原件",
                    )
                elif dataset is not None:
                    first = next(
                        item["first_known_complete_days"]
                        for item in scopes
                        if item["scope_id"] == scope
                    )
                    counts = (first, None, None)
                    state, label = (
                        "blocked_insufficient_samples",
                        "至少需要60个共同样本且高低波动组各30个；尚未完成真实分组核验",
                    )
                models.append(
                    dict(
                        scope_id=scope,
                        state=state,
                        joint_days=counts[0],
                        low_days=counts[1],
                        high_days=counts[2],
                        count_gate_met=counts[0] is not None
                        and counts[0] >= 60
                        and counts[1] is not None
                        and min(counts[1:]) >= 30,
                        method_qualified=False,
                        model_id=identifier,
                        model_hash=model_hash,
                        model_known_at=model_known,
                        blocked_reason_label=label,
                    )
                )
            collection = None
            if boot.epoch_id:
                epoch_value = self._original(session, boot.epoch_id, "epoch")
                row = session.get(Epoch, boot.epoch_id)
                if (
                    epoch_value.plan_id != boot.plan_id
                    or row is None
                    or row.payload_hash != epoch_value.payload_hash
                ):
                    raise ValueError("history_status_epoch_plan_grant_mismatch")
                proof = epoch_value.provenance
                jobs = session.scalars(
                    select(Job)
                    .where(Job.epoch_id == row.epoch_id)
                    .order_by(Job.job_id)
                    .offset(job_offset)
                    .limit(job_limit)
                ).all()
                total = session.scalar(
                    select(func.count()).select_from(Job).where(Job.epoch_id == row.epoch_id)
                )
                deadline = proof["deadline_at"]
                state = (
                    "expired"
                    if datetime.fromisoformat(deadline) <= now and row.state == "active"
                    else row.state
                )
                states = {"pending", "retry", "running", "completed", "partial", "failed"}
                collection = dict(
                    epoch_id=row.epoch_id,
                    state=state,
                    attempts=row.attempts,
                    budget=proof["budget"],
                    remaining_attempts=max(0, proof["budget"] - row.attempts),
                    deadline_at=deadline,
                    paused_reason=row.error_code,
                    paused_reason_label="" if row.error_code is None else _reason(row.error_code),
                    jobs=dict(
                        total=total,
                        offset=job_offset,
                        limit=job_limit,
                        items=[
                            dict(
                                job_id=j.job_id,
                                scope_id=j.scope_id,
                                state=j.state if j.state in states else "unknown",
                                cursor=j.cursor,
                                attempts=j.attempts,
                                page_count=j.page_count,
                                max_pages=j.max_pages,
                                generation=j.generation,
                                error_code=j.error_code,
                            )
                            for j in jobs
                        ],
                    ),
                )
            latest = None
            if grants:
                conditions = [
                    (ForecastRow.scope_id == scope) & (ForecastRow.model_id == model)
                    for scope, model in grants.items()
                ]
                from sqlalchemy import or_

                latest = session.execute(
                    select(ForecastRow.forecast_id, ForecastRow.scope_id, ForecastRow.cutoff_at)
                    .where(
                        ForecastRow.owner_claim == boot.authority.owner_identity,
                        or_(*conditions),
                    )
                    .order_by(ForecastRow.recorded_at.desc())
                    .limit(1)
                ).one_or_none()
        payload = dict(
            schema_version="history-product-status.v2",
            available=True,
            owner_authenticated=True,
            identity_origin="history_signed_session",
            as_of=now,
            historical_pit_verified=False,
            financial_qualified=False,
            data=dict(
                cutoff_count=90,
                known_asof=None if plan is None else plan.known_asof,
                scopes=scopes,
                current_originals_verified=False,
                proof_label="已保存的元数据快照；本次页面刷新未重新核验原始响应或模型",
            ),
            collection=collection,
            models=models,
            forecast=dict(
                state="not_generated" if latest is None else "verification_required",
                forecast_id=None if latest is None else latest.forecast_id,
                scope_id=None if latest is None else latest.scope_id,
                cutoff_at=None if latest is None else latest.cutoff_at.replace(tzinfo=UTC),
                threshold=None,
                baseline_n=None,
                conditional_n=None,
                probabilities=None,
                blocked_reason_label="快状态不生成预测；可信生产器需真实模型和最新31条已收盘分钟原件",
            ),
            excluded_windows=dict(
                total=len(excluded),
                offset=excluded_offset,
                limit=excluded_limit,
                items=excluded[excluded_offset : excluded_offset + excluded_limit],
            ),
            actions=dict(
                can_reconcile=False,
                can_fit=False,
                can_forecast=False,
                can_refresh=boot.allow_producer and bool(grants),
            ),
            user_message="历史原件、首次获知、样本分组和未来预测分别核验；当前未通过历史PIT与金融交付资格。",
        )
        return HistoryProductStatusV2.model_validate_json(
            json.dumps(payload, default=lambda v: v.isoformat())
        )


def history_product_router(bootstrap: HistoryProductBootstrap | None = None):
    router = APIRouter(prefix="/v1/history", tags=["history"])
    status_reader = None if bootstrap is None else HistoryFastStatus(bootstrap)

    def configured():
        if bootstrap is None:
            raise HTTPException(503, detail="history_owner_not_configured")
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
        authenticated(request)
        try:
            return status_reader.status(
                job_offset=job_offset,
                job_limit=job_limit,
                excluded_offset=excluded_offset,
                excluded_limit=excluded_limit,
            )
        except (ValueError, OSError, SQLAlchemyError, KeyError, TypeError):
            raise HTTPException(409, detail="history_status_saved_metadata_unavailable") from None

    @router.post("/assets/{asset}/refresh", response_model=HistoryAssetRefreshReplyV2)
    def refresh(asset: Literal["BTC", "ETH"], request: Request):
        boot = authenticated(request, True)
        if (
            not boot.allow_producer
            or boot.forecasts is None
            or ASSETS[asset] not in boot.forecasts.grants
        ):
            raise HTTPException(403, detail="history_producer_scope_not_granted")
        # Exactly one production call. A transport/writer exception never triggers replay.
        try:
            value = boot.forecasts.producer_once(ASSETS[asset])
        except HistoryDatasetForecastCommittedReadbackError as error:
            return HistoryAssetRefreshReplyV2.model_validate_json(
                json.dumps(
                    dict(
                        schema_version="history-asset-refresh-reply.v2",
                        owner_authenticated=True,
                        asset=asset,
                        outcome="committed_readback_unconfirmed",
                        committed=True,
                        automatic_retry=False,
                        forecast_id=error.forecast_id,
                        cutoff_at=error.cutoff_at.isoformat(),
                        horizon_seconds=86400,
                        target="24h_path_mae",
                        financial_qualified=False,
                        historical_pit_verified=False,
                        fixture_mode=boot.originals.fixture_mode,
                        frequencies=[],
                        user_message="保存已提交，独立读回尚未确认；不会自动重复执行。",
                    )
                )
            )
        except (ValueError, OSError, SQLAlchemyError):
            return HistoryAssetRefreshReplyV2.model_validate_json(
                json.dumps(
                    dict(
                        schema_version="history-asset-refresh-reply.v2",
                        owner_authenticated=True,
                        asset=asset,
                        outcome="unconfirmed",
                        committed=None,
                        automatic_retry=False,
                        forecast_id=None,
                        cutoff_at=None,
                        horizon_seconds=86400,
                        target="24h_path_mae",
                        financial_qualified=False,
                        historical_pit_verified=False,
                        fixture_mode=boot.originals.fixture_mode,
                        frequencies=[],
                        user_message="原件或时点核验未完成，或提交回执未确认；不会自动重复执行。",
                    )
                )
            )
        return _verified_reply(asset, value)

    @router.get("/forecasts/{forecast_id}", response_model=HistoryAssetRefreshReplyV2)
    def original_forecast(forecast_id: str, request: Request):
        boot = authenticated(request)
        if boot.forecasts is None or len(forecast_id) > 128:
            raise HTTPException(403, detail="history_forecast_read_not_granted")
        try:
            with boot.originals.commands._read_session() as session:
                row = session.get(ForecastRow, forecast_id)
                if (
                    row is None
                    or row.owner_claim != boot.authority.owner_identity
                    or (boot.forecasts.grants.get(row.scope_id) != row.model_id)
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
