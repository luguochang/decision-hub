"""Owner-authenticated hourly audit projection, with no forecast/raw reader."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.history_hourly_status_v2 import (
    HistoryHourlyStatusV2,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord as Slot,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope

from .history_owner_authority import HISTORY_COOKIE
from .history_product_routes import HistoryProductBootstrap
from .local_owner_session import LocalOwnerAuthenticationError

ASSETS = {asset: spot_scope(asset + "-USDT").scope_id for asset in ("BTC", "ETH")}
LABELS = {"BTC": "比特币", "ETH": "以太坊"}
STATES = frozenset(
    {"reserved", "blocked", "committed_verified", "committed_readback_unconfirmed", "unconfirmed"}
)


def blocked_label(code):
    if code == "history_hourly_trusted_model_not_configured":
        return (
            "尚无服务器授权的可信模型；历史完整样本需至少60天，波动高低组各30天，不能直接生成概率。"
        )
    if code == "history_hourly_production_not_granted":
        return "服务器尚未授权自动生产；已记录此小时状态，没有生成预测。"
    if code == "history41_qualification_not_server_granted":
        return "历史模型资格尚未确认或未授权，暂不能生成本小时预测。"
    if code in {
        "history_v2_forecast_recent_31_closes_required",
        "missing_closed_original",
        "history_observed_feature_closes_missing",
    }:
        return "最新31个连续已收盘分钟原件不完整或已过期；本小时未生成预测。"
    if code and ("capture" in code or "raw" in code or "readset" in code or "original" in code):
        return "原件、首次获知时点或数据引用核验未通过；本小时未生成预测，需检查来源记录。"
    if code and ("sample" in code or "support" in code or "joint" in code):
        return "有效历史样本未达到60天且高低波动组各30天的门槛；没有生成概率。"
    if code == "history_v2_forecast_model_not_known_at_T":
        return "模型在本次分析时点尚不可知；本小时未生成预测。"
    if code and ("unconfirmed" in code or "fence" in code):
        return "上次提交或回执尚未确认；自动生产已暂停，需要核对已保存结果，不能自动重写。"
    if code and ("deadline" in code or "cancelled" in code):
        return "本次生产达到执行期限或被受管任务取消；写入前已停止，未重试本小时槽。"
    return "本小时未满足预测生产条件；已保留具体原因代码，未伪造成功结果。"


class HistoryHourlyFastStatusV2:
    """At most two indexed latest rows and two <=4 KiB crash-fence files."""

    def __init__(self, bootstrap):
        if type(bootstrap) is not HistoryProductBootstrap:
            raise ValueError("history_hourly_actual_private_bootstrap_required")
        self.bootstrap = bootstrap

    def read(self):
        boot = self.bootstrap
        owner, now = boot.authority.owner_identity, boot.originals.now()
        items = []
        with boot.originals.commands._read_session() as session:
            for asset, scope in ASSETS.items():
                row = (
                    session.execute(
                        select(
                            Slot.slot_id,
                            Slot.scope_id,
                            Slot.hour_at,
                            Slot.cutoff_at,
                            Slot.started_at,
                            Slot.deadline_at,
                            Slot.completed_at,
                            Slot.status,
                            Slot.reason_code,
                            Slot.forecast_id,
                            Slot.model_id,
                            Slot.fixture_mode,
                        )
                        .where(Slot.owner_claim == owner, Slot.scope_id == scope)
                        .order_by(Slot.hour_at.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                item = dict(
                    asset=asset,
                    scope_id=scope,
                    asset_label=LABELS[asset],
                    state="not_due",
                    hour_at=None,
                    cutoff_at=None,
                    started_at=None,
                    deadline_at=None,
                    completed_at=None,
                    slot_id=None,
                    forecast_id=None,
                    model_id=None,
                    reason_code=None,
                    reason_label="已配置独立小时流程，等待受管实时通道的首次记录。",
                    fixture_mode=boot.originals.fixture_mode,
                    fence_present=False,
                )
                if row is not None:
                    if (
                        row["status"] not in STATES
                        or row["fixture_mode"] != boot.originals.fixture_mode
                    ):
                        raise ValueError("history_hourly_status_metadata_invalid")
                    item.update(
                        {
                            name: row[name]
                            for name in (
                                "slot_id",
                                "forecast_id",
                                "model_id",
                                "reason_code",
                                "fixture_mode",
                            )
                        }
                    )
                    item.update(
                        {
                            name: as_utc(row[name])
                            for name in (
                                "hour_at",
                                "cutoff_at",
                                "started_at",
                                "deadline_at",
                                "completed_at",
                            )
                        }
                    )
                    item["state"] = row["status"]
                    item["reason_label"] = {
                        "reserved": "已预留本小时生产槽，等待提交与回执确认。",
                        "committed_verified": (
                            "结果已保存并在提交后核验通过；查看概率时仍需重新读取完整原件。"
                        ),
                    }.get(row["status"], blocked_label(row["reason_code"]))
                    if row["status"] == "reserved" and as_utc(row["deadline_at"]) < now:
                        item.update(
                            state="unconfirmed",
                            reason_code="history_hourly_reservation_deadline_unconfirmed",
                            reason_label=blocked_label(
                                "history_hourly_reservation_deadline_unconfirmed"
                            ),
                        )
                fence = (
                    boot.originals.data_directory
                    / "history-hourly-v2"
                    / "fences"
                    / (canonical_hash([owner, scope]) + ".json")
                )
                if fence.exists():
                    item["fence_present"] = True
                    try:
                        with fence.open("rb") as handle:
                            body = handle.read(4097)
                        if len(body) > 4096:
                            raise ValueError("oversize")
                        proof = json.loads(body)
                        if (proof["owner_claim"], proof["scope_id"]) != (owner, scope):
                            raise ValueError("wrong_owner_or_scope")
                        if row is None or row["slot_id"] != proof["slot_id"]:
                            cutoff = datetime.fromisoformat(proof["cutoff_at"])
                            if cutoff.tzinfo is None or cutoff.utcoffset().total_seconds() != 0:
                                raise ValueError("aware_UTC_required")
                            item.update(
                                slot_id=proof["slot_id"],
                                cutoff_at=cutoff,
                                hour_at=cutoff.replace(minute=0, second=0, microsecond=0),
                                forecast_id=proof["forecast_id"],
                                model_id=None,
                                started_at=None,
                                deadline_at=None,
                                completed_at=None,
                            )
                            item["state"] = "unconfirmed"
                        elif row["status"] in {"blocked", "committed_verified"}:
                            item["state"] = "unconfirmed"
                        elif row["status"] == "reserved" and as_utc(row["deadline_at"]) < now:
                            item["state"] = "unconfirmed"
                        if item["state"] != "reserved":
                            item["reason_code"] = (
                                "history_hourly_unconfirmed_fence_requires_reconciliation"
                            )
                            item["reason_label"] = blocked_label(item["reason_code"])
                    except (ValueError, KeyError, TypeError, OSError):
                        item.update(
                            state="unconfirmed",
                            reason_code="history_hourly_fence_metadata_unavailable",
                            reason_label="上次生产的保护记录不完整，自动生产已暂停，需要人工核对回执。",
                        )
                items.append(item)
        return HistoryHourlyStatusV2.model_validate_json(
            json.dumps(
                dict(
                    schema_version="history-hourly-status.v2",
                    owner_authenticated=True,
                    observed_at=now.isoformat(),
                    cadence_seconds=3600,
                    worker_configured=True,
                    automatic_retry=False,
                    current_forecast_originals_verified=False,
                    financial_qualified=False,
                    historical_pit_verified=False,
                    scopes=items,
                ),
                default=lambda value: value.isoformat(),
            )
        )


def history_hourly_status_router(bootstrap):
    router = APIRouter()
    status = None if bootstrap is None else HistoryHourlyFastStatusV2(bootstrap)

    @router.get("/v1/history/schedule", response_model=HistoryHourlyStatusV2)
    def schedule(request: Request):
        if status is None:
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
            return status.read()
        except Exception:
            raise HTTPException(409, "history_hourly_status_unavailable") from None

    return router
