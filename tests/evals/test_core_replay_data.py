# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

import tools.research_evaluation.build_core_replay_dataset as core_builder
from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityQuery,
    ResearchEvaluationCapabilityFixture,
)
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.provider_adapters.historical import (
    BinanceVisionEventPoint,
    BinanceVisionEventWindow,
    BinanceVisionSource,
    PortaraCrossAssetEventWindowLoader,
    PortaraMacroEventWindowLoader,
)
from packages.provider_adapters.research import (
    CryptoMacroFactPack,
    ReplayResearchCapabilityAdapter,
    load_replay_archive,
)
from packages.provider_adapters.research.replay import ReplayResearchFixture
from tools.research_evaluation.audit_core_replay_dataset import audit
from tools.research_evaluation.build_core_replay_dataset import (
    _target_dataset_id,
    cross_asset_fixture,
    load_macro_window_if_available,
    macro_fixture,
    rate_expectation_fixture,
)


def test_core_builder_derives_versioned_dataset_identity() -> None:
    assert _target_dataset_id("r2r_dev_core_v3") == "crypto_macro.r2r_dev.core.v3"
    assert _target_dataset_id("r2r_holdout_core_v12") == ("crypto_macro.r2r_holdout.core.v12")
    with pytest.raises(ValueError, match="core_replay_target_name_invalid"):
        _target_dataset_id("arbitrary-output")


def test_core_data_coverage_report_records_actual_sources_and_hard_gaps() -> None:
    fixture: dict[str, object] = {
        "evidence_candidates": [
            {
                "source_id": "iceus-dx-via-portara",
                "source_url": "https://example.test/dxy",
            }
        ],
        "facts": [
            {
                "observed_at": "2025-01-15T13:31:00Z",
                "event_offset": "t+1m",
                "attributes": {"source_hash": "a" * 64},
            }
        ],
    }
    covered = core_builder._fixture_coverage(
        fixture,
        importance="hard",
        provider="portara_us2y_dxy",
    )
    hard_gap = core_builder._gap_coverage(
        importance="hard",
        reason="portara_event_window_stale",
    )
    soft_gap = core_builder._gap_coverage(
        importance="soft",
        reason="portara_es_source_not_configured",
    )

    report = core_builder._coverage_report(
        dataset_id="crypto_macro.r2r_dev.core.v4",
        source_timezone="America/Chicago",
        cases=[
            {
                "case_id": "case-1",
                "requirements": {
                    "macro_transmission": covered,
                    "expectation_pricing": hard_gap,
                    "cross_asset_confirmation": soft_gap,
                },
            }
        ],
    )

    summary = cast(dict[str, Any], report["summary"])
    by_requirement = cast(dict[str, dict[str, int]], summary["by_requirement"])
    assert summary["hard_slots"] == 2
    assert summary["covered_hard_slots"] == 1
    assert summary["hard_data_coverage"] == 0.5
    assert by_requirement["expectation_pricing"]["hard_gap"] == 1
    assert by_requirement["cross_asset_confirmation"]["hard_gap"] == 0
    assert cast(dict[str, Any], report["source_contract"])["source_timezone"] == ("America/Chicago")
    assert covered["source_hashes"] == ["a" * 64]
    assert covered["observed_at"] == ["2025-01-15T13:31:00Z"]

    unhashed = dict(report)
    report_hash = unhashed.pop("report_hash")
    canonical = json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()
    assert report_hash == hashlib.sha256(canonical).hexdigest()

    report_path = Path(".r2r_dev_core_v4.building/data_coverage.json")
    with pytest.raises(
        ValueError,
        match=r"core_replay_hard_data_coverage_incomplete:1/2:",
    ):
        core_builder._assert_full_hard_data_coverage(report, report_path=report_path)

    complete = cast(dict[str, Any], report["summary"])
    complete["covered_hard_slots"] = 2
    core_builder._assert_full_hard_data_coverage(report, report_path=report_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dataset", "expected_mean"),
    [
        ("r2r_dev_core_v2", 5 / 18),
        ("r2r_holdout_core_v2", 5 / 18),
        ("r2r_dev_core_v3", 5 / 18),
        ("r2r_holdout_core_v3", 5 / 18),
    ],
)
async def test_core_replay_dataset_applies_existing_source_admission(
    dataset: str, expected_mean: float
) -> None:
    report = await audit(Path("packs/crypto_macro/evaluations") / dataset / "manifest.json")

    assert report["source_mode"] == "reconstructed_fixture"
    assert report["hard_coverage_mean"] == pytest.approx(expected_mean)
    reports = cast(list[dict[str, Any]], report["reports"])
    assert all(item["fact_count"] >= 14 for item in reports)
    # Old 0.75/0.69444/0.80556/0.69444 expectations bypassed admission.
    # The original registry has no Binance Vision/Kalshi archival authorization;
    # parsed facts remain visible but cannot contribute to the coverage score.
    assert all(item["accepted_fact_count"] == 0 for item in reports)
    assert all(
        "crypto_spot_confirmation" not in item["covered_requirement_ids"]
        and "derivatives_crowding" not in item["covered_requirement_ids"]
        for item in reports
    )
    assert all(
        any(
            denial["requirement_id"] == "crypto_spot_confirmation"
            and denial["error_code"] == "research_source_unknown"
            for denial in item["source_denials"]
        )
        for item in reports
    )
    assert report["historical_availability_attested"] is False
    assert all("soft_coverage_ratio" in item and "soft_gaps" in item for item in reports)
    assert all("fact_metric_families" in item for item in reports)
    assert all(isinstance(item["event_family"], str) for item in reports)


@pytest.mark.asyncio
async def test_core_replay_audit_surfaces_cross_asset_soft_gap() -> None:
    report = await audit(
        Path("packs/crypto_macro/evaluations/r2r_dev_core_v3/manifest.json")
    )

    assert "soft_coverage_mean" in report
    reports = cast(list[dict[str, Any]], report["reports"])
    assert all(
        any(
            gap["requirement_id"] == "cross_asset_confirmation"
            for gap in cast(list[dict[str, Any]], item["soft_gaps"])
        )
        for item in reports
    )


def test_core_v3_geopolitical_cases_do_not_require_closed_markets() -> None:
    expected_hard = {
        "event_identity",
        "policy_or_data_delta",
        "crypto_spot_confirmation",
        "derivatives_crowding",
    }
    for relative in (
        "r2r_dev_core_v3/cases/iran_attack_israel_20240413.json",
        "r2r_holdout_core_v3/cases/israel_strikes_iran_20241026.json",
    ):
        case = cast(
            dict[str, Any],
            json.loads(
                (Path("packs/crypto_macro/evaluations") / relative).read_text(encoding="utf-8")
            ),
        )
        assert set(case["expected_hard_requirement_ids"]) == expected_hard
        importance = {
            item["requirement_id"]: item["importance"] for item in case["evidence_requirements"]
        }
        assert importance["expectation_pricing"] == "soft"
        assert importance["macro_transmission"] == "soft"


@pytest.mark.asyncio
async def test_core_builder_never_overwrites_a_frozen_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_builder, "EVALUATIONS", tmp_path)
    target = tmp_path / "r2r_dev_core_v3"
    target.mkdir()
    sentinel = target / "manifest.json"
    sentinel.write_text("frozen", encoding="utf-8")

    with pytest.raises(ValueError, match="core_replay_target_already_exists"):
        await core_builder.build("source-v1", "r2r_dev_core_v3")

    assert sentinel.read_text(encoding="utf-8") == "frozen"


@pytest.mark.asyncio
async def test_core_builder_publishes_a_hashed_full_data_coverage_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source-v1"
    cases_root = source_root / "cases"
    cases_root.mkdir(parents=True)
    case_name = "fomc_cut_25bp_20241107.json"
    original_root = Path("packs/crypto_macro/evaluations/r2r_dev_v1")
    source_case = (original_root / "cases" / case_name).read_bytes()
    (cases_root / case_name).write_bytes(source_case)
    source_manifest = json.loads((original_root / "manifest.json").read_text(encoding="utf-8"))
    source_manifest["fixture_refs"] = [f"cases/{case_name}"]
    source_manifest["fixture_hashes"] = {
        f"cases/{case_name}": hashlib.sha256(source_case).hexdigest()
    }
    source_manifest["event_family_counts"] = {"monetary_policy_decision": 1}
    (source_root / "manifest.json").write_text(json.dumps(source_manifest), encoding="utf-8")

    class FakeBinanceLoader:
        async def load(self, event_at: datetime) -> BinanceVisionEventWindow:
            points = (
                BinanceVisionEventPoint(
                    event_offset="t-5m",
                    observed_at=event_at - timedelta(minutes=5),
                    values={
                        "price": "70000",
                        "volume": "100",
                        "funding_rate": "0.0001",
                        "open_interest": "1000000",
                        "basis": "0.001",
                        "crowding_signal": "1.1",
                    },
                ),
                BinanceVisionEventPoint(
                    event_offset="t+1m",
                    observed_at=event_at + timedelta(minutes=1),
                    values={
                        "price": "70100",
                        "volume": "120",
                        "funding_rate": "0.0001",
                        "open_interest": "1005000",
                        "basis": "0.0011",
                        "crowding_signal": "1.2",
                    },
                ),
            )
            sources = tuple(
                BinanceVisionSource(
                    source_url=url,
                    content_hash=str(index) * 64,
                    member_name=f"source-{index}.csv",
                )
                for index, url in enumerate(
                    (
                        "https://data.binance.vision/data/spot/source.zip",
                        "https://data.binance.vision/data/futures/metrics.zip",
                        "https://data.binance.vision/data/futures/premium.zip",
                        "https://data.binance.vision/data/futures/funding.zip",
                    ),
                    start=1,
                )
            )
            return BinanceVisionEventWindow(
                event_at=event_at,
                points=points,
                sources=sources,
            )

    class MissingKalshiLoader:
        async def load(self, *, event_ticker: str, event_at: datetime) -> None:
            del event_ticker, event_at
            raise ValueError("kalshi_event_markets_missing")

    monkeypatch.setattr(core_builder, "EVALUATIONS", tmp_path)
    monkeypatch.setattr(core_builder, "BinanceVisionEventWindowLoader", FakeBinanceLoader)
    monkeypatch.setattr(core_builder, "KalshiHistoricalExpectationLoader", MissingKalshiLoader)
    rate_path = tmp_path / "USY02Y.txt"
    usd_path = tmp_path / "DXA.txt"
    es_path = tmp_path / "EP.txt"
    _portara_file(
        rate_path,
        [
            ("20241106", "1300", "4.20"),
            ("20241107", "1255", "4.10"),
            ("20241107", "1301", "4.05"),
        ],
    )
    _portara_file(
        usd_path,
        [("20241107", "1255", "104.0"), ("20241107", "1301", "103.9")],
    )
    _portara_file(
        es_path,
        [("20241107", "1255", "5800.0"), ("20241107", "1301", "5780.0")],
    )
    attestation_path = tmp_path / "portara-attestation.json"
    attestation_path.write_text(
        json.dumps(
            {
                "schema_version": "portara-source-attestation.v1",
                "authorization_ref": "owner:test-core-data",
                "provider": "portara-cqg",
                "source_timezone": "America/Chicago",
                "files": {
                    "US2Y": {
                        "sha256": hashlib.sha256(rate_path.read_bytes()).hexdigest(),
                        "source_id": "tullett-prebon-usy02y-via-portara",
                        "source_url": "https://portaracqg.com/fixed-income/int/usy02y",
                    },
                    "DXY": {
                        "sha256": hashlib.sha256(usd_path.read_bytes()).hexdigest(),
                        "source_id": "iceus-dx-via-portara",
                        "source_url": "https://portaracqg.com/futures/int/dxa",
                    },
                    "ES": {
                        "sha256": hashlib.sha256(es_path.read_bytes()).hexdigest(),
                        "source_id": "cme-emini-sp500-es-via-portara",
                        "source_url": "https://portaracqg.com/futures/int/ep",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    manifest_path = await core_builder.build(
        "source-v1",
        "r2r_dev_core_v4",
        portara_us2y_path=rate_path,
        portara_dxy_path=usd_path,
        portara_es_path=es_path,
        portara_attestation_path=attestation_path,
        require_full_hard_data=True,
    )

    coverage = cast(
        dict[str, Any],
        json.loads((manifest_path.parent / "data_coverage.json").read_text(encoding="utf-8")),
    )
    source_hashes = cast(
        dict[str, Any],
        json.loads((manifest_path.parent / "source_hashes.json").read_text(encoding="utf-8")),
    )
    summary = cast(dict[str, Any], coverage["summary"])
    case = cast(list[dict[str, Any]], coverage["cases"])[0]
    expectation = cast(dict[str, Any], case["requirements"])["expectation_pricing"]
    requested = cast(dict[str, list[dict[str, Any]]], case["requested_slots"])
    diagnostics = cast(dict[str, Any], case["diagnostics"])
    assert summary["hard_data_coverage"] == 1
    assert [
        (item["instrument"], item["event_offset"]) for item in requested["macro_transmission"]
    ] == [
        ("US2Y", "t-5m"),
        ("US2Y", "t+1m"),
        ("DXY", "t-5m"),
        ("DXY", "t+1m"),
    ]
    assert all(
        slot["status"] == "available"
        for series in diagnostics["macro"]["series"]
        for slot in series["slots"]
    )
    assert all(slot["status"] == "available" for slot in diagnostics["cross_asset"]["slots"])
    assert expectation["selected_provider"] == "portara_us2y"
    assert expectation["attempts"] == [
        {
            "provider": "kalshi",
            "status": "gap",
            "reason": "kalshi_event_markets_missing",
        },
        {"provider": "portara_us2y", "status": "fixture_built"},
    ]
    assert source_hashes["schema_version"] == "research-source-hashes.v2"
    assert source_hashes["coverage_report_hash"] == coverage["report_hash"]
    assert source_hashes["source_attestation"]["authorization_ref"] == "owner:test-core-data"
    assert coverage["source_contract"]["attestation"]["files"]["US2Y"]["sha256"] == hashlib.sha256(
        rate_path.read_bytes()
    ).hexdigest()
    loaded = load_research_evaluation_dataset(manifest_path)
    assert len(loaded.cases) == 1
    coverage["summary"]["covered_hard_slots"] = 0
    (manifest_path.parent / "data_coverage.json").write_text(
        json.dumps(coverage, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="research_dataset_coverage_hash_mismatch"):
        load_research_evaluation_dataset(manifest_path)

    with pytest.raises(
        ValueError,
        match=r"core_replay_hard_data_coverage_incomplete:2/4:",
    ):
        await core_builder.build(
            "source-v1",
            "r2r_dev_core_v5",
            require_full_hard_data=True,
        )
    assert not (tmp_path / "r2r_dev_core_v5").exists()
    rejected_report = json.loads(
        (tmp_path / ".r2r_dev_core_v5.building" / "data_coverage.json").read_text(encoding="utf-8")
    )
    rejected_summary = cast(dict[str, Any], rejected_report["summary"])
    assert rejected_summary["hard_data_coverage"] == 0.5


@pytest.mark.asyncio
async def test_strict_portara_build_requires_owner_attestation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(core_builder, "EVALUATIONS", tmp_path)
    rate_path = tmp_path / "USY02Y.txt"
    usd_path = tmp_path / "DXA.txt"
    _portara_file(rate_path, [("20241107", "1255", "4.10")])
    _portara_file(usd_path, [("20241107", "1255", "104.0")])

    with pytest.raises(ValueError, match="portara_attestation_required_for_strict_build"):
        await core_builder.build(
            "source-v1",
            "r2r_dev_core_v5",
            portara_us2y_path=rate_path,
            portara_dxy_path=usd_path,
            require_full_hard_data=True,
        )
    assert not (tmp_path / "r2r_dev_core_v5").exists()


@pytest.mark.asyncio
async def test_replay_rebinds_fact_lineage_with_evidence() -> None:
    case_path = Path(
        "packs/crypto_macro/evaluations/r2r_holdout_core_v2/cases/fomc_cut_50bp_20240918.json"
    )
    archive = load_replay_archive(case_path)
    fixture = archive.requirements["crypto_spot_confirmation"]
    original_evidence_id = fixture.evidence_candidates[0].evidence_id
    adapter = ReplayResearchCapabilityAdapter(
        capability_id="replay.research",
        fixtures=archive.queries,
        requirement_fixtures=archive.requirements,
    )
    result = await adapter.execute(
        ResearchCapabilityQuery.model_validate(
            {
                "schema_version": "research-capability-query.v1",
                "request_id": "rebound-test",
                "capability_id": "replay.research",
                "requirement_id": "crypto_spot_confirmation",
                "query": "free model wording",
                "target_url": None,
                "symbols": [],
                "fields": [],
                "allowed_domains": [],
                "max_results": 20,
                "max_cost_usd": 0,
                "research_session_id": "new-session",
                "round": 1,
                "mode": "replay",
                "observed_at": "2024-09-18T18:05:00Z",
                "cutoff_at": "2024-09-18T18:05:00Z",
            }
        )
    )

    rebound_id = result.evidence_candidates[0].evidence_id
    assert rebound_id != original_evidence_id
    assert result.facts
    assert {item.evidence_id for item in result.facts} == {rebound_id}
    assert {item.requirement_id for item in result.facts} == {"crypto_spot_confirmation"}


def _portara_file(path: Path, rows: list[tuple[str, str, str]]) -> None:
    path.write_text(
        "Date,Time,Open,High,Low,Close,Volume\n"
        + "".join(
            f"{date},{time},{close},{close},{close},{close},1\n" for date, time, close in rows
        ),
        encoding="utf-8",
    )


def test_closed_market_portara_window_remains_an_explicit_gap(tmp_path: Path) -> None:
    rate_path = tmp_path / "USY02Y.txt"
    usd_path = tmp_path / "DXA.txt"
    closed_market_rows = [("20240920", "1300", "4.000")]
    _portara_file(rate_path, closed_market_rows)
    _portara_file(usd_path, closed_market_rows)
    loader = PortaraMacroEventWindowLoader(
        us2y_path=rate_path,
        dxy_path=usd_path,
    )

    assert (
        load_macro_window_if_available(
            loader,
            datetime(2024, 9, 21, 18, tzinfo=UTC),
        )
        is None
    )


@pytest.mark.asyncio
async def test_portara_fixtures_pass_replay_semantic_gates(tmp_path: Path) -> None:
    rate_path = tmp_path / "USY02Y.txt"
    usd_path = tmp_path / "DXA.txt"
    _portara_file(
        rate_path,
        [
            ("20240917", "1300", "4.100"),
            ("20240918", "1255", "4.000"),
            ("20240918", "1301", "3.950"),
        ],
    )
    _portara_file(
        usd_path,
        [("20240918", "1255", "100.000"), ("20240918", "1301", "99.800")],
    )
    event_at = datetime(2024, 9, 18, 18, tzinfo=UTC)
    window = PortaraMacroEventWindowLoader(
        us2y_path=rate_path,
        dxy_path=usd_path,
    ).load(event_at)
    archived = ResearchEvaluationCapabilityFixture.model_validate(
        macro_fixture("macro_fixture_gate", window)
    )
    adapter = ReplayResearchCapabilityAdapter(
        capability_id="replay.research",
        fixtures={},
        requirement_fixtures={
            "macro_transmission": ReplayResearchFixture(
                evidence_candidates=tuple(archived.evidence_candidates),
                facts=tuple(archived.facts or ()),
            )
        },
    )
    result = await adapter.execute(
        ResearchCapabilityQuery.model_validate(
            {
                "schema_version": "research-capability-query.v1",
                "request_id": "macro-replay-gate",
                "capability_id": "replay.research",
                "requirement_id": "macro_transmission",
                "query": "model wording is intentionally unrelated to fixture lookup",
                "target_url": None,
                "symbols": [],
                "fields": [],
                "allowed_domains": [],
                "max_results": 20,
                "max_cost_usd": 0,
                "research_session_id": "macro-gate-session",
                "round": 1,
                "mode": "replay",
                "observed_at": "2024-09-18T18:05:00Z",
                "cutoff_at": "2024-09-18T18:05:00Z",
            }
        )
    )
    evidence = [
        item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
        for item in result.evidence_candidates
    ]
    facts = [item.model_copy(update={"quality": "accepted"}) for item in (result.facts or ())]
    requirement = CryptoMacroFactPack.from_pack(Path("packs/crypto_macro")).contract_requirement(
        "macro_transmission"
    )
    assert requirement is not None

    coverage = assess_evidence_sufficiency(
        [requirement],
        evidence,
        cutoff_at=datetime(2024, 9, 18, 18, 5, tzinfo=UTC),
        facts=facts,
    )

    assert coverage.status == "sufficient"
    assert coverage.covered_requirement_ids == ["macro_transmission"]
    assert {item.metric_family for item in facts} == {"macro.rates", "macro.usd"}
    assert {item.independence_group for item in facts} == {
        "tullett-prebon-usy02y-via-portara",
        "iceus-dx-via-portara",
    }

    expectation_archive = ResearchEvaluationCapabilityFixture.model_validate(
        rate_expectation_fixture(
            "rate_expectation_gate",
            PortaraMacroEventWindowLoader(
                us2y_path=rate_path,
                dxy_path=usd_path,
            ).load_rate_expectation(event_at),
        )
    )
    expectation_adapter = ReplayResearchCapabilityAdapter(
        capability_id="replay.research",
        fixtures={},
        requirement_fixtures={
            "expectation_pricing": ReplayResearchFixture(
                evidence_candidates=tuple(expectation_archive.evidence_candidates),
                facts=tuple(expectation_archive.facts or ()),
            )
        },
    )
    expectation_result = await expectation_adapter.execute(
        ResearchCapabilityQuery.model_validate(
            {
                "schema_version": "research-capability-query.v1",
                "request_id": "expectation-replay-gate",
                "capability_id": "replay.research",
                "requirement_id": "expectation_pricing",
                "query": "free model wording",
                "target_url": None,
                "symbols": [],
                "fields": [],
                "allowed_domains": [],
                "max_results": 20,
                "max_cost_usd": 0,
                "research_session_id": "expectation-gate-session",
                "round": 1,
                "mode": "replay",
                "observed_at": "2024-09-18T18:05:00Z",
                "cutoff_at": "2024-09-18T18:05:00Z",
            }
        )
    )
    expectation_evidence = [
        item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
        for item in expectation_result.evidence_candidates
    ]
    expectation_facts = [
        item.model_copy(update={"quality": "accepted"}) for item in (expectation_result.facts or ())
    ]
    expectation_requirement = CryptoMacroFactPack.from_pack(
        Path("packs/crypto_macro")
    ).contract_requirement("expectation_pricing")
    assert expectation_requirement is not None
    expectation_coverage = assess_evidence_sufficiency(
        [expectation_requirement],
        expectation_evidence,
        cutoff_at=datetime(2024, 9, 18, 18, 5, tzinfo=UTC),
        facts=expectation_facts,
    )

    assert expectation_coverage.status == "sufficient"
    assert {item.field for item in expectation_facts} == {"level", "delta"}
    assert {item.event_offset for item in expectation_facts} == {"baseline", "t-5m"}


@pytest.mark.asyncio
async def test_portara_es_fixture_passes_cross_asset_semantic_gate(tmp_path: Path) -> None:
    es_path = tmp_path / "EP.txt"
    _portara_file(
        es_path,
        [("20240918", "1255", "5000.00"), ("20240918", "1301", "4975.00")],
    )
    window = PortaraCrossAssetEventWindowLoader(es_path=es_path).load(
        datetime(2024, 9, 18, 18, tzinfo=UTC)
    )
    archived = ResearchEvaluationCapabilityFixture.model_validate(
        cross_asset_fixture("cross_asset_gate", window)
    )
    adapter = ReplayResearchCapabilityAdapter(
        capability_id="replay.research",
        fixtures={},
        requirement_fixtures={
            "cross_asset_confirmation": ReplayResearchFixture(
                evidence_candidates=tuple(archived.evidence_candidates),
                facts=tuple(archived.facts or ()),
            )
        },
    )
    result = await adapter.execute(
        ResearchCapabilityQuery.model_validate(
            {
                "schema_version": "research-capability-query.v1",
                "request_id": "cross-asset-replay-gate",
                "capability_id": "replay.research",
                "requirement_id": "cross_asset_confirmation",
                "query": "free model wording",
                "target_url": None,
                "symbols": [],
                "fields": [],
                "allowed_domains": [],
                "max_results": 20,
                "max_cost_usd": 0,
                "research_session_id": "cross-asset-gate-session",
                "round": 1,
                "mode": "replay",
                "observed_at": "2024-09-18T18:05:00Z",
                "cutoff_at": "2024-09-18T18:05:00Z",
            }
        )
    )
    evidence = [
        item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
        for item in result.evidence_candidates
    ]
    facts = [item.model_copy(update={"quality": "accepted"}) for item in (result.facts or ())]
    requirement = CryptoMacroFactPack.from_pack(Path("packs/crypto_macro")).contract_requirement(
        "cross_asset_confirmation"
    )
    assert requirement is not None

    coverage = assess_evidence_sufficiency(
        [requirement],
        evidence,
        cutoff_at=datetime(2024, 9, 18, 18, 5, tzinfo=UTC),
        facts=facts,
    )

    assert coverage.status == "sufficient"
    assert coverage.soft_coverage_ratio == 1
    assert {item.metric_family for item in facts} == {"cross_asset.equity"}
