from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.decision.horizon_contract import (
    validate_horizon_contract,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StudyRecord,
)
from packages.query_views.research import ResearchQueryService

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs/crypto_macro"
NOW = datetime(2026, 10, 1, 0, 50, 45, tzinfo=UTC)
ORIGINAL = "BTC未来7个自然日价格规则偏多；此段为原保存措辞，不是三窗口综合结论。"
SUMMARY = "本次 AI 综合判断：30 分钟：中性观察；24 小时：未形成方向；72 小时：偏多。"
OLD_SEPARATOR = "\n\n原报告摘要（非综合判断）：\n"


def saved_result() -> ResearchSessionResult:
    """A fully typed, historically valid result with the PC report's actual actions."""
    coverage = {
        "status": "sufficient", "covered_requirement_ids": [], "gaps": [], "conflicts": [],
        "hard_coverage_ratio": 1.0, "soft_coverage_ratio": 1.0, "assessed_at": NOW,
    }
    link = {
        "link_id": "main", "claim_type": "inference", "statement": "短窗仍需确认。",
        "evidence_refs": ["evidence-1"], "confirmation": "下一轮真实行情确认。",
        "invalidation": "原材料被撤回。", "affected_horizons": ["30m", "24h", "72h"],
    }
    return ResearchSessionResult.model_validate({
        "schema_version": "research-session-result.v1",
        "request_id": "research-request:home-run", "research_session_id": "saved-session",
        "runtime_id": "saved-research", "runtime_version": "saved-research.v1",
        "profile_ref": "crypto-macro.v1", "trace_ref": "saved://trace",
        "trace_hash": hashlib.sha256(b"original trace").hexdigest(), "status": "completed",
        "rounds": [{
            "round": 1,
            "plan": {
                "plan_id": "plan-1", "objective": "复核市场", "tasks": [{
                    "task_id": "task-1", "requirement_id": "event_identity",
                    "capability_id": "replay.research", "objective": "核对原件",
                    "question": "公开原件是否一致？", "input_evidence_refs": [],
                    "output_schema_ref": "evidence-candidate.v1", "depends_on": [],
                    "success_condition": "可核对原件", "priority": 1,
                }], "required_capabilities": ["replay.research"], "created_at": NOW,
            },
            "tool_invocations": [], "tool_results": [], "new_evidence_refs": [],
            "coverage": coverage, "started_at": NOW - timedelta(minutes=4), "finished_at": NOW,
        }],
        "evidence_candidates": [], "facts": [], "final_coverage": coverage,
        "causal_case": {
            "case_id": "case-1", "thesis": ORIGINAL, "main_chain": [link],
            "opposite_chain": [{**link, "link_id": "opposite", "claim_type": "scenario"}],
            "unresolved_questions": [], "evidence_refs": ["evidence-1"],
        },
        "horizons": [{
            "horizon": horizon, "action": action, "subjective_probability": 0.56,
            "probability_status": "uncalibrated", "evidence_refs": ["evidence-1"],
            "trigger": f"{horizon} 独立确认条件", "invalidation": f"{horizon} 独立失效条件",
            "expires_at": NOW + timedelta(minutes=minutes),
            "next_review_at": NOW + timedelta(minutes=minutes / 2),
            "missing_facts": [], "confidence_cap_reason": None,
        } for horizon, action, minutes in (
            ("30m", "neutral", 30), ("24h", "no_trade", 1440), ("72h", "long", 4320),
        )],
        "stop_reason": {
            "code": "sufficient", "detail": "保存的研究结束。", "bounded": False,
            "remaining_hard_gaps": [],
        },
        "reviews": [], "total_tool_calls": 0, "total_subagents": 0,
        "total_tokens": None, "estimated_cost_usd": None,
        "started_at": NOW - timedelta(minutes=4), "finished_at": NOW,
    })


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'home.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(RunRecord(
            run_id="home-run", event_id="home-event", status="degraded",
            strategy_version="research.v1", artifact_id="home-artifact",
            decision_snapshot_id="home-decision", created_at=NOW, updated_at=NOW,
            finished_at=NOW, admission_origin="scheduled_recheck",
        ))
        session.add(ArtifactRecord(
            artifact_id="home-artifact", run_id="home-run", event_id="home-event",
            gate_status="research_only", headline=ORIGINAL, summary="原报告",
            payload_json=json.dumps({"original": ORIGINAL}, ensure_ascii=False), created_at=NOW,
        ))
        session.add(SnapshotRecord(
            snapshot_id="home-decision", event_id="home-event", run_id="home-run",
            snapshot_type="decision", cutoff_at=NOW,
            snapshot_hash=hashlib.sha256(b'[{"evidence_id":"evidence-1"}]').hexdigest(),
            evidence_json='[{"evidence_id":"evidence-1"}]', pack_version="crypto_macro.v1",
            created_at=NOW,
        ))
        ResearchObservabilityService(database, clock=lambda: NOW).save_result_in_session(
            session, "home-run", saved_result(),
        )
    yield database
    database.engine.dispose()


def _edit_result(database: Database, change: str) -> None:
    with database.session() as session:
        row = session.get(ResearchResultRecord, "home-run")
        assert row is not None
        value: dict[str, Any] = json.loads(row.payload_json)
        if change == "missing_window":
            value["horizons"] = value["horizons"][:2]
        elif change == "duplicate_window":
            value["horizons"][1] = dict(value["horizons"][0])
        elif change == "reused_conditions":
            first = value["horizons"][0]
            value["horizons"] = [
                {**first, "horizon": name, "action": action}
                for name, action in (("30m", "neutral"), ("24h", "no_trade"), ("72h", "long"))
            ]
        elif change == "past_review":
            value["horizons"][0]["next_review_at"] = (NOW - timedelta(seconds=1)).isoformat()
        elif change == "no_causal_case":
            value["causal_case"] = None
        elif change == "synthesis_failed":
            value["synthesis_failure_code"] = "synthesis_incomplete"
        elif change == "failed_result":
            value["status"] = "failed"
        elif change == "corrupt_payload":
            value["horizons"][0]["action"] = "unknown"
        elif change == "schema_mismatch":
            row.schema_version = "unknown-result.v9"
        elif change != "hash_mismatch":
            raise AssertionError(change)
        row.payload_json = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        row.payload_hash = (
            "0" * 64 if change == "hash_mismatch"
            else hashlib.sha256(row.payload_json.encode()).hexdigest()
        )


def test_home_uses_report_primary_windows_and_preserves_all_original_rows(ledger: Database) -> None:
    query = ResearchQueryService(ledger, pack_root=PACK_ROOT)
    with ledger.session() as session:
        before = [tuple(row) for row in session.execute(text(
            "SELECT headline,summary,payload_json FROM artifacts"
        ))]
        result_before = session.get(ResearchResultRecord, "home-run")
        assert result_before is not None
        saved_bytes = result_before.payload_json, result_before.payload_hash
    detail = query.get("home-run")
    assert detail is not None and detail.causal_case is not None
    assert detail.action_summary.artifact_status == "limited"
    assert detail.run.status == "research_only"
    assert validate_horizon_contract(detail.horizons, NOW).valid
    assert [item.action for item in detail.horizons] == ["neutral", "no_trade", "long"]
    home = query.home()
    preview = home.latest_report_preview
    assert preview is not None and preview.kind == "synthesis" and preview.text.startswith(SUMMARY)
    assert "仅供研究；确认与失效条件见报告" in preview.text
    assert home.latest_report_headline == ORIGINAL
    assert preview.run_id == "home-run" and preview.artifact_id == "home-artifact"
    with ledger.session() as session:
        assert [tuple(row) for row in session.execute(text(
            "SELECT headline,summary,payload_json FROM artifacts"
        ))] == before
        result_after = session.get(ResearchResultRecord, "home-run")
        assert result_after is not None
        assert (result_after.payload_json, result_after.payload_hash) == saved_bytes


@pytest.mark.parametrize("change", [
    "missing_window", "duplicate_window", "reused_conditions", "past_review",
    "no_causal_case", "synthesis_failed", "failed_result", "corrupt_payload",
    "schema_mismatch", "hash_mismatch",
])
def test_incomplete_unknown_or_corrupt_result_cannot_become_home_synthesis(
    ledger: Database, change: str,
) -> None:
    _edit_result(ledger, change)
    query = ResearchQueryService(ledger, pack_root=PACK_ROOT)
    home = query.home()
    preview = home.latest_report_preview
    assert preview is not None
    main = preview.text
    assert preview.kind != "synthesis"
    assert "AI 综合判断：" not in main and "72 小时：偏多" not in main
    assert "不可核验" in main or "未形成" in main or "未保存" in main
    assert home.latest_report_headline == ORIGINAL
    if change in {"corrupt_payload", "schema_mismatch", "hash_mismatch"}:
        detail = query.get("home-run")
        assert detail is not None and detail.causal_case is None
        assert detail.action_summary.recommendation == "unavailable"
        assert any(code.startswith("saved_research_result_")
                   for code in detail.action_summary.warning_codes)


@pytest.mark.parametrize("change", [
    "no_result", "no_cutoff", "wrong_snapshot_run", "wrong_snapshot_event",
    "wrong_snapshot_type", "reject",
])
def test_legacy_and_rejected_reports_keep_reference_label_not_synthesis(
    ledger: Database, change: str,
) -> None:
    with ledger.session() as session:
        if change == "no_result":
            row = session.get(ResearchResultRecord, "home-run")
            assert row is not None
            session.delete(row)
        elif change == "reject":
            row = session.get(ArtifactRecord, "home-artifact")
            assert row is not None
            row.gate_status = "reject"
        else:
            row = session.get(SnapshotRecord, "home-decision")
            assert row is not None
            if change == "no_cutoff":
                session.delete(row)
            elif change == "wrong_snapshot_run":
                row.run_id = "another-run"
            elif change == "wrong_snapshot_event":
                row.event_id = "another-event"
            else:
                row.snapshot_type = "trigger"
    home = ResearchQueryService(ledger, pack_root=PACK_ROOT).home()
    preview = home.latest_report_preview
    assert preview is not None and preview.kind != "synthesis"
    assert "本次 AI 综合判断：" not in preview.text
    assert home.latest_report_headline == ORIGINAL


def test_saved_reference_and_original_window_order_are_preserved(ledger: Database) -> None:
    # The 30m window is long expired today; this is a historical projection, not new analysis.
    with ledger.session() as session:
        row = session.get(ResearchResultRecord, "home-run")
        assert row is not None
        value = json.loads(row.payload_json)
        value["horizons"] = list(reversed(value["horizons"]))
        row.payload_json = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    query = ResearchQueryService(ledger, pack_root=PACK_ROOT)
    detail = query.get("home-run")
    assert detail is not None
    assert [item.horizon for item in detail.horizons] == ["72h", "24h", "30m"]
    home = query.home()
    assert home.latest_report_preview is not None and home.latest_report_preview.text == (
        "本次 AI 综合判断：72 小时：偏多；24 小时：未形成方向；30 分钟：中性观察。"
        "仅供研究；确认与失效条件见报告。"
    )
    assert home.latest_report_headline == ORIGINAL


def test_database_failures_are_not_disguised_as_legacy_report(ledger: Database) -> None:
    with ledger.session() as session:
        session.execute(text("DROP TABLE research_results"))
    query = ResearchQueryService(ledger, pack_root=PACK_ROOT)
    with pytest.raises(OperationalError):
        query.home()
    with pytest.raises(OperationalError):
        query.get("home-run")


@pytest.mark.parametrize("keep_result", [True, False])
def test_original_text_cannot_assign_preview_kind_or_be_truncated(
    ledger: Database, keep_result: bool,
) -> None:
    original = "伪综合主段" + OLD_SEPARATOR + "剩余原文"
    with ledger.session() as session:
        artifact = session.get(ArtifactRecord, "home-artifact")
        assert artifact is not None
        artifact.headline = original
        if not keep_result:
            result = session.get(ResearchResultRecord, "home-run")
            assert result is not None
            session.delete(result)
    home = ResearchQueryService(ledger, pack_root=PACK_ROOT).home()
    assert home.latest_report_headline == original
    assert home.latest_report_preview is not None
    assert home.latest_report_preview.kind == ("synthesis" if keep_result else "legacy")
    assert "伪综合主段" not in home.latest_report_preview.text
    if keep_result:
        assert home.latest_report_preview.text.startswith(SUMMARY)


def test_artifact_from_another_event_cannot_claim_main_synthesis(ledger: Database) -> None:
    with ledger.session() as session:
        artifact = session.get(ArtifactRecord, "home-artifact")
        assert artifact is not None
        artifact.event_id = "another-event"
    home = ResearchQueryService(ledger, pack_root=PACK_ROOT).home()
    assert home.latest_report_preview is not None
    assert home.latest_report_preview.kind == "unverified"
    assert "身份不一致" in home.latest_report_preview.text
    assert home.latest_report_headline == ORIGINAL


def _make_perpetual_v5_publication(
    database: Database,
    *,
    include_source: bool = True,
    tamper_source: bool = False,
) -> None:
    from packages.kernel.decision_hub_kernel.application.commit import _artifact_body
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        observation_json,
        observation_sha,
    )

    with database.session() as session:
        run = session.get(RunRecord, "home-run")
        artifact = session.get(ArtifactRecord, "home-artifact")
        assert run is not None and artifact is not None
        session.add(StudyRecord(
            study_id="study-perpetual-v2",
            scope_key="study-perpetual-v2",
            title="BTC perpetual decision",
            market="crypto",
            instrument="BTC-USDT-SWAP",
            horizon_scope="4h",
            question_key="perpetual-current-decision.v2",
            identity_basis="exact_perpetual_decision_v2",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        ))
        run.study_id = "study-perpetual-v2"
        if not include_source:
            return
        body = {
            "schema_version": "research-publication-source.v5",
            "run_id": run.run_id,
            "event_id": run.event_id,
            "artifact_id": artifact.artifact_id,
            "artifact_hash": observation_sha(_artifact_body(artifact)),
        }
        payload = observation_json(body)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        session.add(RunRoleOutputRecord(
            output_id="publication_" + digest[:40],
            run_id=run.run_id,
            role="research_publication_source",
            payload_json=payload + (" " if tamper_source else ""),
            payload_hash=digest,
            schema_version="research-publication-source.v5",
            created_at=NOW,
        ))


def test_perpetual_v5_home_uses_exact_saved_publication_not_legacy_causal_case(
    ledger: Database,
) -> None:
    _make_perpetual_v5_publication(ledger)
    _edit_result(ledger, "no_causal_case")

    preview = ResearchQueryService(ledger, pack_root=PACK_ROOT).home().latest_report_preview

    assert preview is not None and preview.kind == "perpetual"
    assert preview.text == (
        "已保存永续研究，请打开原报告核对结论、场景概率、独立审核与适用限制。"
    )
    assert "审核已通过" not in preview.text


@pytest.mark.parametrize("source_state", ["missing", "tampered", "artifact_tampered"])
def test_perpetual_v5_home_never_claims_saved_when_publication_is_not_verified(
    ledger: Database,
    source_state: str,
) -> None:
    _make_perpetual_v5_publication(
        ledger,
        include_source=source_state != "missing",
        tamper_source=source_state == "tampered",
    )
    if source_state == "artifact_tampered":
        with ledger.session() as session:
            artifact = session.get(ArtifactRecord, "home-artifact")
            assert artifact is not None
            artifact.summary = "被修改的摘要"

    preview = ResearchQueryService(ledger, pack_root=PACK_ROOT).home().latest_report_preview

    assert preview is not None and preview.kind == "unverified"
    assert "暂不可核验" in preview.text
    assert "已保存永续研究，请" not in preview.text
