from __future__ import annotations

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate

LATEST_ADVICE_SOURCE_ID = "decision-desk-latest-advice"
LATEST_ADVICE_EVENT_HINT = "crypto_market_snapshot"
LATEST_ADVICE_TEMPLATE = (
    "BTC 最新操作建议（固定研究模板）。截至本次执行时自动获取 BTC 现货价格与成交量、"
    "已收盘日线和可用的永续资金费率、未平仓量、基差、拥挤度，并检索可能解释当前波动的"
    "最新公开信息。以未来 7 个自然日为主周期，输出主要方向（偏多、偏空、区间或证据不足）、"
    "当前行动候选、主机制、最强反方、触发条件、失效条件、关键证据和数据限制。"
    "必须把四件事分别写清：市场方向、研究可信度、当前入场/持仓倾向、交易执行权限。"
    "衍生品或宏观缺失时，说明它限制的是哪一层；不能把缺口直接改写成方向不存在，"
    "也不能把方向观点改写成已经授权下单。不得把未校准的主观概率称为胜率。"
)

_DEFAULT_PROMPTS = frozenset(
    {
        "获取最新操作建议",
        "给我最新操作建议",
        "最新操作建议",
        "现在怎么操作",
        "目前怎么操作",
        "当前怎么操作",
    }
)
_NORMALIZED_DEFAULT_PROMPTS = frozenset(
    "".join(item.casefold().split()) for item in _DEFAULT_PROMPTS
)


def normalize_latest_advice_request(request: ObservationCreate) -> ObservationCreate:
    """Expand a terse Desk request into one auditable BTC research job.

    The Decision Desk is a focused BTC decision surface. A user should not
    have to know the internal evidence vocabulary to request the current view.
    Explicit non-snapshot event hints remain authoritative for API clients that
    submit a different event workflow.
    """

    if request.event_hint and request.event_hint != LATEST_ADVICE_EVENT_HINT:
        return request
    text = request.text.strip()
    fixed_entry = request.source_id == LATEST_ADVICE_SOURCE_ID
    if not fixed_entry and not _looks_like_latest_advice(text):
        return request

    note = "" if "".join(text.casefold().split()) in _NORMALIZED_DEFAULT_PROMPTS else text
    expanded = LATEST_ADVICE_TEMPLATE
    if note:
        expanded = f"{expanded}\n用户补充关注点：{note}"
    return request.model_copy(
        update={
            "text": expanded,
            "source_id": LATEST_ADVICE_SOURCE_ID,
            "event_hint": LATEST_ADVICE_EVENT_HINT,
        }
    )


def _looks_like_latest_advice(text: str) -> bool:
    if not text or len(text) > 160:
        return False
    normalized = "".join(text.casefold().split())
    if normalized in _NORMALIZED_DEFAULT_PROMPTS:
        return True
    current = any(
        marker in normalized for marker in ("现在", "目前", "当前", "最新", "today", "now")
    )
    action = any(
        marker in normalized
        for marker in ("操作", "建议", "买", "卖", "做多", "做空", "观望", "long", "short")
    )
    return current and action
