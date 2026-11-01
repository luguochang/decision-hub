"""Project durable research artifacts into a user-facing explanation graph.

This module intentionally exposes only attested artifacts (evidence, causal
links, coverage and gates).  It is not a model-thought viewer and must never
invent a causal relationship when synthesis was rejected or incomplete.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from packages.contracts_py.decision_hub_contracts import (
    DecisionGraph,
    DecisionGraphEdge,
    DecisionGraphNode,
)


def _node(
    node_id: str,
    node_type: str,
    label: str,
    status: str,
    summary: str,
    evidence_refs: Iterable[str] = (),
) -> DecisionGraphNode:
    return DecisionGraphNode(
        node_id=node_id,
        node_type=node_type,
        label=label[:200],
        status=status,
        summary=summary[:2000],
        evidence_refs=list(dict.fromkeys(ref for ref in evidence_refs if ref)),
    )


def _edge(edge_id: str, source: str, target: str, relation: str, label: str) -> DecisionGraphEdge:
    return DecisionGraphEdge(
        edge_id=edge_id,
        from_node=source,
        to_node=target,
        relation=relation,
        label=label[:300],
    )


def _evidence_group_key(item: Any) -> tuple[str, str]:
    """Keep one graph node per source/requirement pair.

    A graph node per raw excerpt becomes unreadable as soon as a run collects
    more than a handful of sources. Grouping is still fully auditable because
    the node retains every evidence id in ``evidence_refs`` and the detail
    page renders the individual records.
    """

    return (
        str(getattr(item, "source_id", "unknown-source")),
        str(getattr(item, "requirement_id", "unknown-requirement")),
    )


def _evidence_node_id(index: int) -> str:
    return f"evidence-source-{index}"


def _stop_reason(result: Any, run_view: Any, action_summary: Any) -> str | None:
    reason = getattr(result, "stop_reason", None)
    if reason is not None:
        detail = getattr(reason, "detail", None)
        code = getattr(reason, "code", None)
        if detail and code:
            return f"{code}: {detail}"
        if detail:
            return detail
    reason = getattr(run_view, "stop_reason", None)
    if reason is not None:
        return getattr(reason, "detail", None) or getattr(reason, "code", None)
    warnings = getattr(action_summary, "warning_codes", None) or []
    return ", ".join(warnings) if warnings else None


def build_decision_graph(
    *,
    run_view: Any,
    evidence: list[Any],
    causal_case: Any | None,
    horizons: list[Any],
    action_summary: Any,
    coverage: Any | None = None,
    result: Any | None = None,
    reviews: list[Any] | None = None,
) -> DecisionGraph:
    """Build a deterministic graph from a run's canonical read model.

    A completed causal case gets a ``causal`` graph.  Every other outcome gets
    a bounded evidence/rule projection with explicit unresolved and gate nodes.
    """

    nodes: list[DecisionGraphNode] = [
        _node(
            "trigger",
            "trigger",
            "研究触发",
            "confirmed",
            getattr(run_view, "event_title", "最新市场研究请求"),
        )
    ]
    edges: list[DecisionGraphEdge] = []
    graph_status = "causal" if causal_case is not None else "bounded_fallback"
    generation_mode = "causal_case" if causal_case is not None else "evidence_projection"

    if causal_case is not None:
        # The causal projection used to jump straight from the trigger to the
        # claims. That hid the actual information points even though the run
        # had durable Evidence records. Keep claims readable, but put grouped
        # source nodes on the graph and connect them to the claims that cite
        # them. The raw records remain in the Evidence section below.
        accepted = [
            item
            for item in evidence
            if getattr(item, "quality", None) in {"accepted", "candidate"}
        ]
        grouped: dict[tuple[str, str], list[Any]] = {}
        for item in accepted:
            grouped.setdefault(_evidence_group_key(item), []).append(item)
        evidence_groups: list[tuple[str, list[Any]]] = []
        for index, ((_source_id, requirement_id), items) in enumerate(grouped.items(), 1):
            node_id = _evidence_node_id(index)
            evidence_groups.append((node_id, items))
            refs = [getattr(item, "evidence_id", "") for item in items]
            first = items[0]
            authority = getattr(first, "authority", "unknown")
            kind = getattr(first, "kind", "evidence")
            excerpts = [str(getattr(item, "excerpt", "")).strip() for item in items]
            excerpt = next((value for value in excerpts if value), "已接收证据")
            summary = (
                f"来源={_source_id}；要求={requirement_id}；类型={kind}；"
                f"权威={authority}；{excerpt}"
            )
            nodes.append(
                _node(
                    node_id,
                    "evidence",
                    str(_source_id),
                    (
                        "confirmed"
                        if all(getattr(item, "quality", None) == "accepted" for item in items)
                        else "supporting"
                    ),
                    summary,
                    refs,
                )
            )
            edges.append(
                _edge(f"{node_id}-trigger", "trigger", node_id, "derived_from", "来源采集")
            )

        chain_terminals: dict[str, str] = {}
        for side, links, status, relation in (
            ("main", getattr(causal_case, "main_chain", []), "supporting", "supports"),
            ("opposite", getattr(causal_case, "opposite_chain", []), "opposing", "contradicts"),
        ):
            previous = "trigger"
            for index, link in enumerate(links, 1):
                node_id = f"{side}-claim-{index}"
                refs = getattr(link, "evidence_refs", [])
                nodes.append(
                    _node(
                        node_id,
                        "causal_claim",
                        f"{'主链' if side == 'main' else '反方'} {index}",
                        status,
                        getattr(link, "statement", ""),
                        refs,
                    )
                )
                edges.append(
                    _edge(
                        f"{node_id}-edge",
                        previous,
                        node_id,
                        relation,
                        getattr(link, "claim_type", "推导"),
                    )
                )
                previous = node_id

                # Make the evidence-to-claim relationship visible without
                # pretending that the graph is a hidden model thought trace.
                link_refs = set(refs)
                for evidence_node_id, evidence_items in evidence_groups:
                    group_refs = {
                        getattr(item, "evidence_id", "") for item in evidence_items
                    }
                    if link_refs.intersection(group_refs):
                        edges.append(
                            _edge(
                                f"{evidence_node_id}-{node_id}",
                                evidence_node_id,
                                node_id,
                                relation,
                                "主链证据" if side == "main" else "反方证据",
                            )
                        )
            chain_terminals[side] = previous
        thesis = getattr(causal_case, "thesis", "已形成因果假设")
        nodes.append(
            _node(
                "thesis",
                "action",
                "综合判断",
                "derived",
                thesis,
                getattr(causal_case, "evidence_refs", []),
            )
        )
        edges.append(
            _edge(
                "main-to-thesis",
                chain_terminals["main"],
                "thesis",
                "derived_from",
                "主链汇总",
            )
        )
        if chain_terminals["opposite"] != "trigger":
            edges.append(
                _edge("opposite-to-thesis", chain_terminals["opposite"], "thesis", "bounded_by", "反方约束")
            )

        if coverage is not None:
            coverage_status = getattr(coverage, "status", "unknown")
            nodes.append(
                _node(
                    "coverage",
                    "coverage",
                    "证据覆盖",
                    "confirmed" if coverage_status == "sufficient" else "unresolved",
                    (
                        f"状态={coverage_status}；"
                        f"硬覆盖={getattr(coverage, 'hard_coverage_ratio', 0):.0%}；"
                        f"软覆盖={getattr(coverage, 'soft_coverage_ratio', 0):.0%}"
                    ),
                    getattr(causal_case, "evidence_refs", []),
                )
            )
            edges.append(_edge("thesis-coverage", "thesis", "coverage", "bounded_by", "覆盖复核"))

        for index, review in enumerate(reviews or [], 1):
            review_id = f"review-{index}"
            resolution = getattr(review, "resolution", "bounded")
            review_status = "confirmed" if resolution == "accepted" else "unresolved"
            limitations = getattr(review, "limitations", []) or []
            summary = (
                f"结论={resolution}；挑战={getattr(review, 'challenge', '')}"
                + (f"；限制={'；'.join(limitations)}" if limitations else "")
            )
            refs = getattr(review, "evidence_refs", [])
            nodes.append(
                _node(
                    review_id,
                    "review",
                    f"独立复核 {index}",
                    review_status,
                    summary,
                    refs,
                )
            )
            edges.append(
                _edge(f"{review_id}-thesis", review_id, "thesis", "reviews", "复核综合判断")
            )
    else:
        generation_mode = (
            "rule_projection"
            if getattr(action_summary, "research_outlook", None)
            else "evidence_projection"
        )
        accepted = [
            item for item in evidence if getattr(item, "quality", None) in {"accepted", "candidate"}
        ]
        for index, item in enumerate(accepted[:40], 1):
            node_id = f"evidence-{index}"
            nodes.append(
                _node(
                    node_id,
                    "evidence",
                    getattr(item, "source_id", f"证据 {index}"),
                    "confirmed" if getattr(item, "quality", None) == "accepted" else "supporting",
                    getattr(item, "excerpt", "已接收证据"),
                    [getattr(item, "evidence_id", "")],
                )
            )
            edges.append(
                _edge(f"evidence-edge-{index}", "trigger", node_id, "derived_from", "采集证据")
            )
        coverage_status = getattr(coverage, "status", None) or getattr(run_view, "stage", "unknown")
        coverage_id = "coverage"
        nodes.append(
            _node(
                coverage_id,
                "coverage",
                "证据覆盖",
                "confirmed" if coverage_status == "sufficient" else "unresolved",
                str(coverage_status),
                [],
            )
        )
        for item in nodes:
            if item.node_type == "evidence":
                edges.append(
                    _edge(
                        f"{item.node_id}-coverage",
                        item.node_id,
                        coverage_id,
                        "supports",
                        "覆盖评估",
                    )
                )
        nodes.append(
            _node(
                "unresolved",
                "stop",
                "未闭合问题",
                "blocked",
                _stop_reason(result, run_view, action_summary) or "综合校验未形成可审计因果链",
            )
        )
        edges.append(
            _edge("coverage-unresolved", coverage_id, "unresolved", "bounded_by", "质量门")
        )
        for index, review in enumerate(reviews or [], 1):
            review_id = f"review-{index}"
            resolution = getattr(review, "resolution", "bounded")
            review_status = "confirmed" if resolution == "accepted" else "unresolved"
            limitations = getattr(review, "limitations", []) or []
            summary = (
                f"结论={resolution}；挑战={getattr(review, 'challenge', '')}"
                + (f"；限制={'；'.join(limitations)}" if limitations else "")
            )
            nodes.append(
                _node(
                    review_id,
                    "review",
                    f"独立复核 {index}",
                    review_status,
                    summary,
                    getattr(review, "evidence_refs", []),
                )
            )
            edges.append(
                _edge(f"{review_id}-coverage", review_id, coverage_id, "reviews", "复核覆盖结论")
            )

    for index, horizon in enumerate(horizons[:3], 1):
        horizon_id = f"horizon-{index}"
        action = getattr(horizon, "action", "no_trade")
        nodes.append(
            _node(
                horizon_id,
                "horizon",
                str(getattr(horizon, "horizon", f"窗口 {index}")),
                "derived" if action != "no_trade" else "unresolved",
                (
                    f"方向={action}；触发={getattr(horizon, 'trigger', '')}；"
                    f"失效={getattr(horizon, 'invalidation', '')}"
                ),
                getattr(horizon, "evidence_refs", []),
            )
        )
        edges.append(
            _edge(
                f"thesis-{horizon_id}",
                "thesis" if causal_case is not None else "coverage",
                horizon_id,
                "follows",
                "时间窗口判断",
            )
        )

    outlook = getattr(action_summary, "research_outlook", None)
    if outlook is not None:
        nodes.append(
            _node(
                "outlook",
                "action",
                "方向性展望",
                "derived",
                f"{getattr(outlook, 'outlook', 'unresolved')}；{getattr(outlook, 'rationale', '')}",
                getattr(outlook, "evidence_refs", []),
            )
        )
        edges.append(
            _edge(
                "outlook-edge",
                "thesis" if causal_case is not None else "coverage",
                "outlook",
                "follows",
                "规则/统计展望",
            )
        )

    nodes.append(
        _node(
            "execution-gate",
            "gate",
            "执行质量门",
            "blocked" if not getattr(action_summary, "trade_authorized", False) else "confirmed",
            getattr(action_summary, "headline", "执行权限由质量门决定"),
            getattr(action_summary, "supporting_evidence_refs", []),
        )
    )
    edges.append(
        _edge(
            "gate-edge",
            "outlook"
            if outlook is not None
            else ("thesis" if causal_case is not None else "unresolved"),
            "execution-gate",
            "gates",
            "交易权限",
        )
    )
    # The graph is a bounded display projection; the canonical result keeps
    # the complete stop detail.
    stop_reason = _stop_reason(result, run_view, action_summary)
    if stop_reason is not None and len(stop_reason) > 1000:
        stop_reason = f"{stop_reason[:999]}…"
    return DecisionGraph(
        schema_version="decision-graph.v1",
        graph_status=graph_status,
        generation_mode=generation_mode,
        nodes=nodes,
        edges=edges,
        stop_reason=stop_reason,
    )
