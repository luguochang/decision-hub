from __future__ import annotations

from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts.models import ResearchSessionResult

_HORIZONS = {"30m": "30 分钟", "24h": "24 小时", "72h": "72 小时"}
_ACTIONS = {"long": "偏多", "short": "偏空", "neutral": "中性观察", "no_trade": "不交易"}
_CLAIMS = {"fact": "原研究事实陈述", "inference": "推断", "scenario": "情景"}
_STATUSES = {"completed": "已完成", "degraded": "有限完成", "failed": "失败", "cancelled": "已取消"}
_GAPS = {
    "missing": "缺少资料", "stale": "资料过期", "low_authority": "来源可信度不足",
    "insufficient_sources": "独立来源不足", "conflict": "资料冲突",
    "tool_unavailable": "取证工具不可用", "semantic_mismatch": "资料口径不符",
    "no_baseline": "缺少比较基线", "window_missing": "缺少指定时间窗口",
}


def _refs(refs: list[str]) -> str:
    # These are the original object's links, not newly certified references.
    return "（原引用：" + "、".join(refs) + "）"


def _time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def render_research_report_summary(
    result: ResearchSessionResult,
    *,
    publishable_research: bool,
) -> str:
    """Render saved research content; grant neither Action nor probability authority.

    This function is pure and does not repair/reassess the supplied result. Caller
    publication validation remains responsible for original-reference authority.
    A successful research publication is distinct from an executable ActionPlan.
    """
    case = result.causal_case
    usable_chain = (
        case is not None
        and result.status not in {"failed", "cancelled"}
        and result.synthesis_failure_code is None
        and result.stop_reason.code != "synthesis_incomplete"
    )
    complete = usable_chain and publishable_research
    lines = ["结论", f"研究结果时点：{_time(result.finished_at)}。"]
    if usable_chain and case is not None:
        lines.append(
            ("研究结论：" if complete else "待验证假设：") + case.thesis + _refs(case.evidence_refs)
        )
        if not complete:
            lines.append("当前仅有待验证研究假设，尚不满足完整研究发布条件。")
        for horizon in result.horizons:
            label = "研究判断" if complete else "待验证方向"
            lines.append(
                f"{_HORIZONS[horizon.horizon]}{label}：{_ACTIONS[horizon.action]}"
                + _refs(horizon.evidence_refs)
            )
        lines.append("以上为研究判断；可执行方案及其资格以单独发布的行动方案为准。")
    else:
        lines.append("本次未形成可用的完整因果结论，不提供方向性操作判断。")

    lines.extend(["", "依据"])
    if usable_chain and case is not None:
        lines.extend(
            f"{index}. [{_CLAIMS[link.claim_type]}] {link.statement}"
            + _refs(link.evidence_refs)
            for index, link in enumerate(case.main_chain, 1)
        )
    else:
        accepted = [item for item in result.evidence_candidates if item.quality == "accepted"]
        if accepted:
            lines.append("以下材料已被原结果接纳，尚未形成有效的综合论证：")
            lines.extend(item.excerpt + _refs([item.evidence_id]) for item in accepted)
        else:
            lines.append("原结果未列出可展示的已接纳材料；不能据此补造依据。")

    lines.extend(["", "反证"])
    if usable_chain and case is not None:
        lines.extend(
            f"{index}. [{_CLAIMS[link.claim_type]}] {link.statement}"
            + _refs(link.evidence_refs)
            for index, link in enumerate(case.opposite_chain, 1)
        )
    else:
        lines.append("未形成可用的反方因果链。")

    lines.extend(["", "触发失效"])
    if usable_chain and case is not None:
        for title, chain in (("主链", case.main_chain), ("反链", case.opposite_chain)):
            for index, link in enumerate(chain, 1):
                lines.append(
                    f"{title}第 {index} 项确认：{link.confirmation}；失效：{link.invalidation}"
                )
        for horizon in result.horizons:
            lines.extend([
                f"{_HORIZONS[horizon.horizon]}研究条件：{horizon.trigger}；失效：{horizon.invalidation}",
                f"原有效截止：{_time(horizon.expires_at)}；原复核时点：{_time(horizon.next_review_at)}。",
            ])
        if not result.horizons:
            lines.append("原结果未形成分期限的操作条件。")
        lines.append("条件为原研究声明，未在此确认已触发、成交或当前仍有效。")
    else:
        lines.append("缺少有效综合结果，不将残留条件作为可执行方案。")

    lines.extend(["", "未解问题"])
    unresolved = list(case.unresolved_questions) if case is not None else []
    for gap in result.final_coverage.gaps:
        unresolved.append(f"{_GAPS[gap.reason_code]}：{gap.query_hint}")
    for conflict in result.final_coverage.conflicts:
        if conflict.resolution_status != "resolved":
            unresolved.append("尚未解决的冲突：" + conflict.summary + _refs(conflict.evidence_refs))
    unresolved.extend("尚缺硬性要求：" + item for item in result.stop_reason.remaining_hard_gaps)
    for horizon in result.horizons:
        unresolved.extend(
            f"{_HORIZONS[horizon.horizon]}待补事实：{item}" for item in horizon.missing_facts
        )
        if horizon.confidence_cap_reason:
            unresolved.append(f"{_HORIZONS[horizon.horizon]}置信限制：{horizon.confidence_cap_reason}")
    lines.extend(dict.fromkeys(unresolved) or ["原结果未另列未解问题，不代表没有风险。"])
    if any(item.probability_status == "uncalibrated" for item in result.horizons):
        lines.append("原研究包含未校准概率，不能作为已验证胜率。")
    lines.extend([
        "", "停止原因", f"研究状态：{_STATUSES[result.status]}。", result.stop_reason.detail,
    ])
    return "\n".join(lines)
