from __future__ import annotations

from types import SimpleNamespace

from packages.query_views.research.decision_graph import build_decision_graph


def _summary(*, outlook=None):
    return SimpleNamespace(
        warning_codes=["synthesis_incomplete"],
        trade_authorized=False,
        headline="证据不足，交易权限阻断",
        supporting_evidence_refs=["ev-1"],
        research_outlook=outlook,
    )


def test_fallback_graph_exposes_evidence_coverage_and_gate_without_inventing_causal_chain():
    run = SimpleNamespace(
        event_title="BTC latest advice",
        stage="assessing_sufficiency",
        stop_reason=None,
    )
    evidence = [
        SimpleNamespace(
            evidence_id="ev-1",
            source_id="binance",
            quality="accepted",
            excerpt="spot snapshot",
        )
    ]
    graph = build_decision_graph(
        run_view=run,
        evidence=evidence,
        causal_case=None,
        horizons=[],
        action_summary=_summary(),
        coverage=SimpleNamespace(status="insufficient"),
    )

    assert graph.graph_status == "bounded_fallback"
    assert graph.generation_mode == "evidence_projection"
    assert {node.node_id for node in graph.nodes} >= {
        "trigger",
        "coverage",
        "unresolved",
        "execution-gate",
    }
    assert not any(node.node_type == "causal_claim" for node in graph.nodes)


def test_causal_graph_keeps_main_and_opposite_chain_distinct():
    run = SimpleNamespace(event_title="BTC macro event", stage="done", stop_reason=None)
    def link(statement: str, ref: str) -> SimpleNamespace:
        return SimpleNamespace(
            statement=statement,
            evidence_refs=[ref],
            claim_type="inference",
        )
    case = SimpleNamespace(
        thesis="流动性收紧压制风险资产",
        evidence_refs=["ev-main", "ev-counter"],
        main_chain=[link("美元走强", "ev-main")],
        opposite_chain=[link("风险偏好回升", "ev-counter")],
    )
    evidence = [
        SimpleNamespace(
            evidence_id="ev-main",
            source_id="macro-feed",
            requirement_id="macro_transmission",
            kind="market",
            authority="official",
            quality="accepted",
            excerpt="美元走强与收益率上行",
        ),
        SimpleNamespace(
            evidence_id="ev-counter",
            source_id="derivatives-feed",
            requirement_id="derivatives_crowding",
            kind="market",
            authority="exchange",
            quality="accepted",
            excerpt="风险偏好回升",
        ),
    ]
    graph = build_decision_graph(
        run_view=run,
        evidence=evidence,
        causal_case=case,
        horizons=[],
        action_summary=_summary(),
        coverage=SimpleNamespace(
            status="sufficient",
            hard_coverage_ratio=1.0,
            soft_coverage_ratio=0.5,
        ),
        reviews=[SimpleNamespace(
            resolution="accepted",
            challenge="反方证据不足以推翻主链",
            limitations=["执行数据仍需人工复核"],
            evidence_refs=["ev-main"],
        )],
    )

    assert graph.graph_status == "causal"
    assert graph.generation_mode == "causal_case"
    assert {node.node_id for node in graph.nodes} >= {"main-claim-1", "opposite-claim-1", "thesis"}
    assert {node.node_id for node in graph.nodes} >= {
        "evidence-source-1",
        "evidence-source-2",
        "coverage",
        "review-1",
    }
    assert any(edge.relation == "supports" for edge in graph.edges)
    assert any(edge.relation == "contradicts" for edge in graph.edges)
    assert any(edge.relation == "reviews" for edge in graph.edges)
