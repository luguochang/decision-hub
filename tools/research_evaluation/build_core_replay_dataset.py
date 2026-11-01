from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvaluationDatasetManifest,
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
)
from packages.kernel.decision_hub_kernel.application.evolution import (
    dataset_manifest_hash,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.provider_adapters.historical import (
    BinanceVisionEventPoint,
    BinanceVisionEventWindow,
    BinanceVisionEventWindowLoader,
    KalshiExpectationWindow,
    KalshiHistoricalExpectationLoader,
    PortaraCrossAssetEventWindow,
    PortaraCrossAssetEventWindowLoader,
    PortaraMacroEventWindow,
    PortaraMacroEventWindowLoader,
    PortaraRateExpectationWindow,
    PortaraSourceAttestation,
)
from packages.provider_adapters.research import CryptoMacroFactPack

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"
EVALUATIONS = PACK_ROOT / "evaluations"
TARGET_POLICIES = {
    "expectation_pricing",
    "crypto_spot_confirmation",
    "derivatives_crowding",
    "macro_transmission",
    "cross_asset_confirmation",
}
KALSHI_EVENTS = {
    "fomc_cut_25bp_20241107": "FED-24NOV",
    "fomc_cut_25bp_20241218": "FED-24DEC",
    "fomc_cut_50bp_20240918": "FED-24SEP",
    "us_cpi_20250115": "KXCPI-24DEC",
    "us_cpi_20250212": "KXCPI-25JAN",
    "us_jobs_20250207": "KXPAYROLLS-25JAN",
    "us_jobs_20250307": "KXPAYROLLS-25FEB",
    "us_pce_20250228": "KXPCECORE-25JAN",
}

_EXPECTED_ARCHIVE_GAPS = {
    "portara_event_window_missing",
    "portara_event_window_stale",
}


def _attempt(provider: str, status: str, reason: str | None = None) -> dict[str, str]:
    result = {"provider": provider, "status": status}
    if reason is not None:
        result["reason"] = reason
    return result


def _fixture_coverage(
    fixture: dict[str, object],
    *,
    importance: str,
    provider: str,
    attempts: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    evidence = cast(list[dict[str, Any]], fixture.get("evidence_candidates", []))
    facts = cast(list[dict[str, Any]], fixture.get("facts", []))
    source_hashes = sorted(
        {
            str(attributes["source_hash"])
            for fact in facts
            if isinstance((attributes := fact.get("attributes")), dict)
            and attributes.get("source_hash")
        }
    )
    result: dict[str, object] = {
        "status": "fixture_built",
        "importance": importance,
        "selected_provider": provider,
        "evidence_count": len(evidence),
        "fact_count": len(facts),
        "source_ids": sorted(
            {str(item["source_id"]) for item in evidence if item.get("source_id")}
        ),
        "source_refs": sorted(
            {str(item["source_url"]) for item in evidence if item.get("source_url")}
        ),
        "source_hashes": source_hashes,
        "observed_at": sorted(
            {str(item["observed_at"]) for item in facts if item.get("observed_at")}
        ),
        "event_offsets": sorted(
            {str(item["event_offset"]) for item in facts if item.get("event_offset")}
        ),
    }
    if attempts:
        result["attempts"] = attempts
    return result


def _gap_coverage(
    *,
    importance: str,
    reason: str,
    attempts: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "gap",
        "importance": importance,
        "reason": reason,
        "evidence_count": 0,
        "fact_count": 0,
        "source_ids": [],
        "source_refs": [],
        "source_hashes": [],
        "observed_at": [],
        "event_offsets": [],
    }
    if attempts:
        result["attempts"] = attempts
    return result


def requested_data_slots(event_at: datetime) -> dict[str, list[dict[str, object]]]:
    event = event_at.astimezone(UTC)
    event_window = (
        ("t-5m", event - timedelta(minutes=5)),
        ("t+1m", event + timedelta(minutes=1)),
    )
    return {
        "expectation_pricing": [
            {
                "instrument": "US2Y",
                "event_offset": "baseline",
                "target_at": (event - timedelta(hours=24)).isoformat(),
                "maximum_lag_seconds": 259200,
            },
            {
                "instrument": "US2Y",
                "event_offset": "t-5m",
                "target_at": (event - timedelta(minutes=5)).isoformat(),
                "maximum_lag_seconds": 90,
            },
        ],
        "macro_transmission": [
            {
                "instrument": instrument,
                "event_offset": event_offset,
                "target_at": target.isoformat(),
                "maximum_lag_seconds": 90,
            }
            for instrument in ("US2Y", "DXY")
            for event_offset, target in event_window
        ],
        "cross_asset_confirmation": [
            {
                "instrument": "ES",
                "event_offset": event_offset,
                "target_at": target.isoformat(),
                "maximum_lag_seconds": 90,
            }
            for event_offset, target in event_window
        ],
    }


def _coverage_report(
    *,
    dataset_id: str,
    source_timezone: str,
    cases: list[dict[str, object]],
    source_attestation: dict[str, object] | None = None,
) -> dict[str, object]:
    by_requirement: dict[str, dict[str, int]] = {}
    hard_slots = 0
    covered_hard_slots = 0
    for case in cases:
        requirements = cast(dict[str, dict[str, object]], case["requirements"])
        for requirement_id, item in requirements.items():
            counters = by_requirement.setdefault(
                requirement_id,
                {"fixture_built": 0, "gap": 0, "hard_gap": 0},
            )
            status = str(item["status"])
            counters[status] += 1
            if item["importance"] == "hard":
                hard_slots += 1
                if status == "fixture_built":
                    covered_hard_slots += 1
                else:
                    counters["hard_gap"] += 1
    report: dict[str, object] = {
        "schema_version": "core-data-coverage.v1",
        "dataset_id": dataset_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_contract": {
            "delivery": "owner-configured licensed Portara/CQG ASCII",
            "columns": ["Date", "Time", "Open", "High", "Low", "Close", "Volume"],
            "source_timezone": source_timezone,
            "macro_files": {"US2Y": "USY02Y", "DXY": "DX/DXA"},
            "optional_cross_asset_file": {"ES": "EP"},
        },
        "summary": {
            "case_count": len(cases),
            "hard_slots": hard_slots,
            "covered_hard_slots": covered_hard_slots,
            "hard_data_coverage": (covered_hard_slots / hard_slots if hard_slots else 1.0),
            "by_requirement": by_requirement,
        },
        "cases": cases,
    }
    if source_attestation is not None:
        cast(dict[str, object], report["source_contract"])["attestation"] = source_attestation
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_hash"] = hashlib.sha256(canonical).hexdigest()
    return report


def _assert_full_hard_data_coverage(report: dict[str, object], *, report_path: Path) -> None:
    summary = cast(dict[str, object], report["summary"])
    hard_slots = int(cast(int, summary["hard_slots"]))
    covered_hard_slots = int(cast(int, summary["covered_hard_slots"]))
    if covered_hard_slots != hard_slots:
        raise ValueError(
            "core_replay_hard_data_coverage_incomplete:"
            f"{covered_hard_slots}/{hard_slots}:{report_path}"
        )


def _verify_portara_attestation(
    attestation: PortaraSourceAttestation,
    *,
    us2y_path: Path | None,
    dxy_path: Path | None,
    es_path: Path | None,
    source_timezone: str,
) -> dict[str, object]:
    """Bind owner-declared Portara identity to the exact input file bytes."""

    if attestation.source_timezone != source_timezone:
        raise ValueError("portara_attestation_timezone_mismatch")
    if us2y_path is None or dxy_path is None:
        raise ValueError("portara_attestation_macro_files_required")
    expected = {
        "US2Y": (
            us2y_path,
            "tullett-prebon-usy02y-via-portara",
            PortaraMacroEventWindowLoader.rate_source_url,
        ),
        "DXY": (
            dxy_path,
            "iceus-dx-via-portara",
            PortaraMacroEventWindowLoader.usd_source_url,
        ),
    }
    if es_path is not None:
        expected["ES"] = (
            es_path,
            "cme-emini-sp500-es-via-portara",
            PortaraCrossAssetEventWindowLoader.source_url,
        )
    if set(attestation.files) != set(expected):
        raise ValueError("portara_attestation_roles_mismatch")
    for role, (path, source_id, source_url) in expected.items():
        declared = attestation.verify_file(role, path)
        if declared.source_id != source_id or declared.source_url != source_url:
            raise ValueError(f"portara_attestation_identity_mismatch:{role}")
    return {
        "schema_version": "portara-source-attestation.v1",
        "provider": attestation.provider,
        "authorization_ref": attestation.authorization_ref,
        "source_timezone": attestation.source_timezone,
        "files": {
            role: {
                "sha256": attestation.files[role].sha256,
                "source_id": attestation.files[role].source_id,
                "source_url": attestation.files[role].source_url,
            }
            for role in sorted(expected)
        },
    }


async def build(
    source_name: str,
    target_name: str,
    *,
    portara_us2y_path: Path | None = None,
    portara_dxy_path: Path | None = None,
    portara_es_path: Path | None = None,
    portara_attestation_path: Path | None = None,
    portara_timezone: str = "America/Chicago",
    require_full_hard_data: bool = False,
) -> Path:
    target_version = _target_version(target_name)
    source_root = _dataset_root(source_name)
    final_target_root = _dataset_root(target_name)
    if (portara_us2y_path is None) != (portara_dxy_path is None):
        raise ValueError("portara_macro_files_must_be_configured_together")
    source_attestation: PortaraSourceAttestation | None = None
    if portara_attestation_path is not None:
        source_attestation = PortaraSourceAttestation.from_path(portara_attestation_path)
    if require_full_hard_data and portara_us2y_path is not None and source_attestation is None:
        raise ValueError("portara_attestation_required_for_strict_build")
    source_attestation_payload = (
        _verify_portara_attestation(
            source_attestation,
            us2y_path=portara_us2y_path,
            dxy_path=portara_dxy_path,
            es_path=portara_es_path,
            source_timezone=portara_timezone,
        )
        if source_attestation is not None
        else None
    )
    if final_target_root.exists():
        raise ValueError("core_replay_target_already_exists")
    target_root = _dataset_root(f".{target_name}.building")
    if target_root.exists():
        shutil.rmtree(target_root)
    cases_root = target_root / "cases"
    cases_root.mkdir(parents=True)

    source_manifest = cast(
        dict[str, Any],
        json.loads((source_root / "manifest.json").read_text(encoding="utf-8")),
    )
    fact_pack = CryptoMacroFactPack.from_pack(PACK_ROOT)
    loader = BinanceVisionEventWindowLoader()
    expectation_loader = KalshiHistoricalExpectationLoader()
    macro_loader = (
        PortaraMacroEventWindowLoader(
            us2y_path=portara_us2y_path,
            dxy_path=portara_dxy_path,
            source_timezone=portara_timezone,
        )
        if portara_us2y_path is not None and portara_dxy_path is not None
        else None
    )
    cross_asset_loader = (
        PortaraCrossAssetEventWindowLoader(
            es_path=portara_es_path,
            source_timezone=portara_timezone,
        )
        if portara_es_path is not None
        else None
    )
    fixture_hashes: dict[str, str] = {}
    all_source_hashes: dict[str, str] = {}
    case_coverage: list[dict[str, object]] = []
    for fixture_ref in source_manifest["fixture_refs"]:
        source_case = cast(
            dict[str, Any],
            json.loads((source_root / str(fixture_ref)).read_text(encoding="utf-8")),
        )
        policies = {
            item.requirement_id: item
            for item in fact_pack.contract_requirements_for_event(str(source_case["event_family"]))
        }
        event_at = datetime.fromisoformat(source_case["cutoff_at"]) - timedelta(minutes=5)
        window = await loader.load(event_at)
        source_case["evidence_requirements"] = [
            _replace_policy(item, policies) for item in source_case["evidence_requirements"]
        ]
        source_case["expected_hard_requirement_ids"] = [
            item["requirement_id"]
            for item in source_case["evidence_requirements"]
            if item["importance"] == "hard"
        ]
        additions = _crypto_fixtures(source_case["case_id"], window)
        event_ticker = KALSHI_EVENTS.get(source_case["case_id"])
        expectation: KalshiExpectationWindow | None = None
        expectation_attempts: list[dict[str, str]] = []
        if event_ticker is not None:
            try:
                expectation = await expectation_loader.load(
                    event_ticker=event_ticker, event_at=event_at
                )
                expectation_attempts.append(_attempt("kalshi", "fixture_built"))
            except ValueError as exc:
                if str(exc) not in {
                    "kalshi_event_markets_missing",
                    "kalshi_expectation_window_missing",
                }:
                    raise
                expectation_attempts.append(_attempt("kalshi", "gap", str(exc)))
        else:
            expectation_attempts.append(
                _attempt("kalshi", "gap", "kalshi_event_contract_not_mapped")
            )
        if expectation is not None:
            additions.append(_expectation_fixture(source_case["case_id"], expectation))
        macro_window: PortaraMacroEventWindow | None = None
        macro_gap_reason = "portara_macro_source_not_configured"
        macro_diagnostics: dict[str, object] | None = None
        if macro_loader is not None:
            macro_window, macro_gap_reason = _load_macro_window(macro_loader, event_at)
            macro_diagnostics = macro_loader.coverage(event_at)
        if macro_window is not None:
            additions.append(macro_fixture(source_case["case_id"], macro_window))
        rate_expectation: PortaraRateExpectationWindow | None = None
        rate_expectation_gap_reason = "portara_macro_source_not_configured"
        if macro_loader is not None and expectation is None:
            rate_expectation, rate_expectation_gap_reason = _load_rate_expectation(
                macro_loader, event_at
            )
            expectation_attempts.append(
                _attempt(
                    "portara_us2y",
                    "fixture_built" if rate_expectation is not None else "gap",
                    rate_expectation_gap_reason,
                )
            )
        elif expectation is None:
            expectation_attempts.append(
                _attempt("portara_us2y", "gap", rate_expectation_gap_reason)
            )
        if rate_expectation is not None:
            additions.append(rate_expectation_fixture(source_case["case_id"], rate_expectation))
        cross_asset_window: PortaraCrossAssetEventWindow | None = None
        cross_asset_gap_reason = "portara_es_source_not_configured"
        cross_asset_diagnostics: dict[str, object] | None = None
        if cross_asset_loader is not None:
            cross_asset_window, cross_asset_gap_reason = _load_cross_asset_window(
                cross_asset_loader, event_at
            )
            cross_asset_diagnostics = cross_asset_loader.coverage(event_at)
        if cross_asset_window is not None:
            additions.append(cross_asset_fixture(source_case["case_id"], cross_asset_window))
        fixtures_by_requirement = {str(item["requirement_id"]): item for item in additions}
        requirement_coverage: dict[str, dict[str, object]] = {}
        provider_by_requirement = {
            "crypto_spot_confirmation": "binance_vision",
            "derivatives_crowding": "binance_vision",
            "macro_transmission": "portara_us2y_dxy",
            "cross_asset_confirmation": "portara_es",
            "expectation_pricing": ("kalshi" if expectation is not None else "portara_us2y"),
        }
        gap_reason_by_requirement = {
            "macro_transmission": macro_gap_reason,
            "cross_asset_confirmation": cross_asset_gap_reason,
            "expectation_pricing": (
                rate_expectation_gap_reason
                if expectation is None
                else "expectation_fixture_missing"
            ),
        }
        for requirement_id in TARGET_POLICIES:
            importance = str(policies[requirement_id].importance)
            fixture = fixtures_by_requirement.get(requirement_id)
            attempts = expectation_attempts if requirement_id == "expectation_pricing" else None
            if fixture is not None:
                requirement_coverage[requirement_id] = _fixture_coverage(
                    fixture,
                    importance=importance,
                    provider=provider_by_requirement[requirement_id],
                    attempts=attempts,
                )
            else:
                requirement_coverage[requirement_id] = _gap_coverage(
                    importance=importance,
                    reason=(gap_reason_by_requirement.get(requirement_id) or "fixture_not_built"),
                    attempts=attempts,
                )
        case_coverage.append(
            {
                "case_id": str(source_case["case_id"]),
                "event_family": str(source_case["event_family"]),
                "event_at": event_at.isoformat(),
                "requested_slots": requested_data_slots(event_at),
                "diagnostics": {
                    "macro": macro_diagnostics,
                    "cross_asset": cross_asset_diagnostics,
                },
                "requirements": requirement_coverage,
            }
        )
        source_case["archived_capability_fixtures"].extend(additions)
        urls = [item.source_url for item in window.sources]
        if expectation is not None:
            urls.extend(expectation.source_urls)
        if macro_window is not None:
            urls.extend(series.source_url for series in macro_window.series)
        if rate_expectation is not None:
            urls.append(rate_expectation.source_url)
        if cross_asset_window is not None:
            urls.append(cross_asset_window.source_url)
        source_case["source_refs"] = list(dict.fromkeys([*source_case["source_refs"], *urls]))
        for source in window.sources:
            previous = all_source_hashes.setdefault(source.source_url, source.content_hash)
            if previous != source.content_hash:
                raise ValueError("binance_vision_source_hash_conflict")
        if expectation is not None:
            for url, source_hash in zip(
                expectation.source_urls, expectation.source_hashes, strict=True
            ):
                previous = all_source_hashes.setdefault(url, source_hash)
                if previous != source_hash:
                    raise ValueError("kalshi_source_hash_conflict")
        if macro_window is not None:
            for series in macro_window.series:
                previous = all_source_hashes.setdefault(series.source_url, series.source_hash)
                if previous != series.source_hash:
                    raise ValueError("portara_source_hash_conflict")
        if rate_expectation is not None:
            previous = all_source_hashes.setdefault(
                rate_expectation.source_url, rate_expectation.source_hash
            )
            if previous != rate_expectation.source_hash:
                raise ValueError("portara_source_hash_conflict")
        if cross_asset_window is not None:
            previous = all_source_hashes.setdefault(
                cross_asset_window.source_url, cross_asset_window.source_hash
            )
            if previous != cross_asset_window.source_hash:
                raise ValueError("portara_source_hash_conflict")
        target_case = cases_root / Path(fixture_ref).name
        target_case.write_text(
            json.dumps(source_case, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        relative = f"cases/{target_case.name}"
        fixture_hashes[relative] = hashlib.sha256(target_case.read_bytes()).hexdigest()

    fixture_refs = list(fixture_hashes)
    manifest_values = {
        **source_manifest,
        "dataset_id": _target_dataset_id(target_name),
        "fixture_refs": fixture_refs,
        "fixture_hashes": fixture_hashes,
        "source_mode": "reconstructed_fixture",
        "authorization": f"owner:core-data-reconstruction-{target_version}",
        "created_at": datetime.now(UTC).isoformat(),
    }
    manifest_values["manifest_hash"] = dataset_manifest_hash(
        str(manifest_values["split"]),
        fixture_refs,
        str(manifest_values["cutoff_rule"]),
        fixture_hashes=fixture_hashes,
        label_rule=str(manifest_values["label_rule"]),
        leakage_audit=str(manifest_values["leakage_audit"]),
        authorization=str(manifest_values["authorization"]),
        visibility=str(manifest_values["visibility"]),
        source_mode=str(manifest_values["source_mode"]),
        window_start_at=datetime.fromisoformat(str(manifest_values["window_start_at"])),
        window_end_at=datetime.fromisoformat(str(manifest_values["window_end_at"])),
        event_family_counts=cast(dict[str, int], manifest_values["event_family_counts"]),
    )
    EvaluationDatasetManifest.model_validate(manifest_values)
    coverage_report = _coverage_report(
        dataset_id=str(manifest_values["dataset_id"]),
        source_timezone=portara_timezone,
        cases=case_coverage,
        source_attestation=source_attestation_payload,
    )
    (target_root / "manifest.json").write_text(
        json.dumps(manifest_values, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    coverage_report_path = target_root / "data_coverage.json"
    coverage_encoded = (
        json.dumps(coverage_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    coverage_report_path.write_bytes(coverage_encoded)
    source_hash_payload: dict[str, object] = {
        "schema_version": "research-source-hashes.v2",
        "dataset_id": manifest_values["dataset_id"],
        "sources": all_source_hashes,
        "coverage_report_ref": "data_coverage.json",
        "coverage_report_hash": coverage_report["report_hash"],
        "coverage_report_file_hash": hashlib.sha256(coverage_encoded).hexdigest(),
    }
    if source_attestation_payload is not None:
        source_hash_payload["source_attestation"] = source_attestation_payload
    (target_root / "source_hashes.json").write_text(
        json.dumps(
            source_hash_payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if require_full_hard_data:
        _assert_full_hard_data_coverage(coverage_report, report_path=coverage_report_path)
    target_root.rename(final_target_root)
    return final_target_root / "manifest.json"


def _dataset_root(name: str) -> Path:
    if re.fullmatch(r"\.?[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
        raise ValueError("core_replay_dataset_name_invalid")
    return EVALUATIONS / name


def _target_version(name: str) -> str:
    matched = re.fullmatch(r"r2r_(?:dev|holdout)_core_(v[1-9][0-9]*)", name)
    if matched is None:
        raise ValueError("core_replay_target_name_invalid")
    return matched.group(1)


def _target_dataset_id(name: str) -> str:
    matched = re.fullmatch(r"r2r_(dev|holdout)_core_(v[1-9][0-9]*)", name)
    if matched is None:
        raise ValueError("core_replay_target_name_invalid")
    return f"crypto_macro.r2r_{matched.group(1)}.core.{matched.group(2)}"


def load_macro_window_if_available(
    loader: PortaraMacroEventWindowLoader, event_at: datetime
) -> PortaraMacroEventWindow | None:
    """Keep closed-market cases as explicit gaps while rejecting broken archives."""

    return _load_macro_window(loader, event_at)[0]


def _load_macro_window(
    loader: PortaraMacroEventWindowLoader, event_at: datetime
) -> tuple[PortaraMacroEventWindow | None, str | None]:
    try:
        return loader.load(event_at), None
    except ValueError as exc:
        if str(exc) in _EXPECTED_ARCHIVE_GAPS:
            return None, str(exc)
        raise


def load_cross_asset_window_if_available(
    loader: PortaraCrossAssetEventWindowLoader, event_at: datetime
) -> PortaraCrossAssetEventWindow | None:
    """Keep closed-market cross-asset observations as explicit gaps."""

    return _load_cross_asset_window(loader, event_at)[0]


def _load_cross_asset_window(
    loader: PortaraCrossAssetEventWindowLoader, event_at: datetime
) -> tuple[PortaraCrossAssetEventWindow | None, str | None]:
    try:
        return loader.load(event_at), None
    except ValueError as exc:
        if str(exc) in _EXPECTED_ARCHIVE_GAPS:
            return None, str(exc)
        raise


def load_rate_expectation_if_available(
    loader: PortaraMacroEventWindowLoader, event_at: datetime
) -> PortaraRateExpectationWindow | None:
    """Return a rate-pricing fallback only when its real pre-event bar exists."""

    return _load_rate_expectation(loader, event_at)[0]


def _load_rate_expectation(
    loader: PortaraMacroEventWindowLoader, event_at: datetime
) -> tuple[PortaraRateExpectationWindow | None, str | None]:
    try:
        return loader.load_rate_expectation(event_at), None
    except ValueError as exc:
        if str(exc) in _EXPECTED_ARCHIVE_GAPS:
            return None, str(exc)
        raise


def _replace_policy(
    raw: dict[str, object], policies: dict[str, EvidenceRequirement]
) -> dict[str, object]:
    requirement_id = str(raw["requirement_id"])
    if requirement_id not in TARGET_POLICIES:
        return raw
    return policies[requirement_id].model_dump(mode="json")


def _crypto_fixtures(case_id: str, window: BinanceVisionEventWindow) -> list[dict[str, object]]:
    spot_source = next(
        item
        for item in window.sources
        if item.source_url.startswith("https://data.binance.vision/data/spot/")
    )
    derivative_sources = [item for item in window.sources if item is not spot_source]
    spot_values = {
        point.event_offset: {
            "price": point.values["price"],
            "volume": point.values["volume"],
        }
        for point in window.points
    }
    derivative_values = {
        point.event_offset: {
            key: point.values[key]
            for key in ("funding_rate", "open_interest", "basis", "crowding_signal")
        }
        for point in window.points
    }
    return [
        _fixture(
            case_id=case_id,
            requirement_id="crypto_spot_confirmation",
            source_url=spot_source.source_url,
            source_hashes=[spot_source.content_hash],
            points=window.points,
            values=spot_values,
            instrument="BTCUSDT",
            venue="binance",
            metric_family="crypto.spot",
            units={"price": "usdt", "volume": "usdt", "event_return": "percent"},
            derived=("event_return", "price"),
        ),
        _fixture(
            case_id=case_id,
            requirement_id="derivatives_crowding",
            source_url=derivative_sources[0].source_url,
            source_hashes=[item.content_hash for item in derivative_sources],
            points=window.points,
            values=derivative_values,
            instrument="BTCUSDT-PERP",
            venue="binance",
            metric_family="crypto.derivatives",
            units={
                "funding_rate": "rate",
                "open_interest": "usdt",
                "open_interest_delta": "percent",
                "basis": "rate",
                "crowding_signal": "ratio",
            },
            derived=("open_interest_delta", "open_interest"),
        ),
    ]


def _fixture(
    *,
    case_id: str,
    requirement_id: str,
    source_url: str,
    source_hashes: list[str],
    points: Sequence[BinanceVisionEventPoint],
    values: dict[str, dict[str, str]],
    instrument: str,
    venue: str,
    metric_family: str,
    units: dict[str, str],
    derived: tuple[str, str],
) -> dict[str, object]:
    observed_at = max(point.observed_at for point in points)
    received_at = observed_at + timedelta(seconds=1)
    archive_hash = hashlib.sha256(
        json.dumps(
            {"source_hashes": source_hashes, "values": values},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    excerpt = json.dumps(
        {
            "archive": "Binance Vision",
            "case_id": case_id,
            "event_window": values,
            "reconstruction_mode": "reconstructed_history",
            "source_hashes": source_hashes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = research_evidence_content_hash(
        requirement_id=requirement_id,
        kind="market",
        authority="exchange",
        source_id="binance-vision",
        source_url=source_url,
        published_at=observed_at,
        excerpt=excerpt,
        structured_payload_ref=f"binance-vision://{archive_hash}",
    )
    evidence_id = f"archive_{content_hash[:32]}"
    candidate = EvidenceCandidate(
        evidence_id=evidence_id,
        requirement_id=requirement_id,
        kind="market",
        authority="exchange",
        source_id="binance-vision",
        source_url=AnyUrl(source_url),
        published_at=observed_at,
        observed_at=observed_at,
        received_at=received_at,
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=f"binance-vision://{archive_hash}",
        tool_call_id=f"archive:{case_id}:{requirement_id}",
        research_session_id=f"archive:{case_id}",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )
    facts: list[FactEnvelope] = []
    for point in points:
        for field, value in values[point.event_offset].items():
            facts.append(
                _fact(
                    candidate,
                    field=field,
                    value=value,
                    unit=units[field],
                    metric_family=metric_family,
                    instrument=instrument,
                    venue=venue,
                    event_offset=point.event_offset,
                    observed_at=point.observed_at,
                    source_hash=archive_hash,
                    event_id=case_id,
                )
            )
    derived_field, baseline_field = derived
    baseline = Decimal(values["t-5m"][baseline_field])
    post = Decimal(values["t+1m"][baseline_field])
    delta = str((post / baseline - Decimal(1)) * Decimal(100))
    facts.append(
        _fact(
            candidate,
            field=derived_field,
            value=delta,
            unit=units[derived_field],
            metric_family=metric_family,
            instrument=instrument,
            venue=venue,
            event_offset="t+1m",
            observed_at=observed_at,
            source_hash=archive_hash,
            event_id=case_id,
            window_start_at=min(point.observed_at for point in points),
            window_end_at=observed_at,
        )
    )
    return {
        "capability_id": "replay.research",
        "requirement_id": requirement_id,
        "query_aliases": [
            f"{case_id}:{requirement_id}",
            f"Acquire typed {requirement_id} event-window evidence for {case_id}",
        ],
        "evidence_candidates": [candidate.model_dump(mode="json")],
        "facts": [item.model_dump(mode="json") for item in facts],
    }


def _expectation_fixture(case_id: str, window: KalshiExpectationWindow) -> dict[str, object]:
    values = {item.event_offset: item.probability for item in window.points}
    latest = next(item for item in window.points if item.event_offset == "t-5m")
    baseline = next(item for item in window.points if item.event_offset == "baseline")
    delta = str(Decimal(latest.probability) - Decimal(baseline.probability))
    archive_hash = hashlib.sha256(
        json.dumps(
            {
                "event_ticker": window.event_ticker,
                "market_ticker": window.market_ticker,
                "source_hashes": window.source_hashes,
                "values": values,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    excerpt = json.dumps(
        {
            "archive": "Kalshi historical market candlesticks",
            "event_ticker": window.event_ticker,
            "market_ticker": window.market_ticker,
            "market_title": window.market_title,
            "probability": values,
            "delta_percentage_point": delta,
            "selection": "highest 30-hour pre-event traded volume with both offsets",
            "reconstruction_mode": "reconstructed_history",
            "source_hashes": window.source_hashes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = research_evidence_content_hash(
        requirement_id="expectation_pricing",
        kind="market",
        authority="exchange",
        source_id="kalshi-exchange",
        source_url=window.source_urls[1],
        published_at=latest.observed_at,
        excerpt=excerpt,
        structured_payload_ref=f"kalshi-archive://{archive_hash}",
    )
    evidence_id = f"archive_{content_hash[:32]}"
    candidate = EvidenceCandidate(
        evidence_id=evidence_id,
        requirement_id="expectation_pricing",
        kind="market",
        authority="exchange",
        source_id="kalshi-exchange",
        source_url=AnyUrl(window.source_urls[1]),
        published_at=latest.observed_at,
        observed_at=latest.observed_at,
        received_at=latest.observed_at + timedelta(seconds=1),
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=f"kalshi-archive://{archive_hash}",
        tool_call_id=f"archive:{case_id}:expectation_pricing",
        research_session_id=f"archive:{case_id}",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )
    facts = [
        _fact(
            candidate,
            field="level",
            value=point.probability,
            unit="probability",
            metric_family="macro.policy_expectation",
            instrument=window.market_ticker,
            venue="kalshi",
            event_offset=point.event_offset,
            observed_at=point.observed_at,
            source_hash=archive_hash,
            event_id=case_id,
        )
        for point in window.points
    ]
    facts.append(
        _fact(
            candidate,
            field="delta",
            value=delta,
            unit="percentage_point",
            metric_family="macro.policy_expectation",
            instrument=window.market_ticker,
            venue="kalshi",
            event_offset="t-5m",
            observed_at=latest.observed_at,
            source_hash=archive_hash,
            event_id=case_id,
            window_start_at=baseline.observed_at,
            window_end_at=latest.observed_at,
        )
    )
    return {
        "capability_id": "replay.research",
        "requirement_id": "expectation_pricing",
        "query_aliases": [
            f"{case_id}:expectation_pricing",
            f"Acquire typed expectation pricing evidence for {case_id}",
        ],
        "evidence_candidates": [candidate.model_dump(mode="json")],
        "facts": [item.model_dump(mode="json") for item in facts],
    }


def macro_fixture(case_id: str, window: PortaraMacroEventWindow) -> dict[str, object]:
    candidates: list[EvidenceCandidate] = []
    facts: list[FactEnvelope] = []
    for series in window.series:
        observed_at = max(point.observed_at for point in series.points)
        values = {
            point.event_offset: {
                "level": point.level,
                "event_return": point.event_return,
            }
            for point in series.points
        }
        excerpt = json.dumps(
            {
                "archive": "Portara/CQG licensed ASCII",
                "case_id": case_id,
                "delivery_vendor": "portara-cqg",
                "instrument": series.instrument,
                "market_origin": series.venue,
                "source_timezone": window.source_timezone,
                "event_window": values,
                "reconstruction_mode": "reconstructed_history",
                "source_hash": series.source_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        structured_ref = f"portara-cqg://sha256/{series.source_hash}"
        content_hash = research_evidence_content_hash(
            requirement_id="macro_transmission",
            kind="market",
            authority="exchange",
            source_id=series.source_id,
            source_url=series.source_url,
            published_at=observed_at,
            excerpt=excerpt,
            structured_payload_ref=structured_ref,
        )
        candidate = EvidenceCandidate(
            evidence_id=f"archive_{content_hash[:32]}",
            requirement_id="macro_transmission",
            kind="market",
            authority="exchange",
            source_id=series.source_id,
            source_url=AnyUrl(series.source_url),
            published_at=observed_at,
            observed_at=observed_at,
            received_at=observed_at + timedelta(seconds=1),
            content_hash=content_hash,
            excerpt=excerpt,
            structured_payload_ref=structured_ref,
            tool_call_id=f"archive:{case_id}:macro_transmission",
            research_session_id=f"archive:{case_id}",
            round=1,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        candidates.append(candidate)
        for point in series.points:
            for field, value, unit in (
                ("level", point.level, series.level_unit),
                ("event_return", point.event_return, series.event_return_unit),
            ):
                facts.append(
                    _fact(
                        candidate,
                        field=field,
                        value=value,
                        unit=unit,
                        metric_family=series.metric_family,
                        instrument=series.instrument,
                        venue=series.venue,
                        event_offset=point.event_offset,
                        observed_at=point.observed_at,
                        source_hash=series.source_hash,
                        event_id=case_id,
                        source_timezone=window.source_timezone,
                        window_start_at=(
                            series.points[0].observed_at if field == "event_return" else None
                        ),
                        window_end_at=(point.observed_at if field == "event_return" else None),
                    )
                )
    return {
        "capability_id": "replay.research",
        "requirement_id": "macro_transmission",
        "query_aliases": [
            f"{case_id}:macro_transmission",
            f"Acquire typed rates and USD event-window evidence for {case_id}",
        ],
        "evidence_candidates": [item.model_dump(mode="json") for item in candidates],
        "facts": [item.model_dump(mode="json") for item in facts],
    }


def rate_expectation_fixture(
    case_id: str, window: PortaraRateExpectationWindow
) -> dict[str, object]:
    latest = next(point for point in window.points if point.event_offset == "t-5m")
    values = {
        point.event_offset: {"level": point.level, "delta_bps": point.delta}
        for point in window.points
    }
    excerpt = json.dumps(
        {
            "archive": "Portara/CQG licensed ASCII",
            "case_id": case_id,
            "delivery_vendor": "portara-cqg",
            "instrument": window.instrument,
            "market_origin": window.venue,
            "source_timezone": window.source_timezone,
            "pre_event_rate_repricing": values,
            "reconstruction_mode": "reconstructed_history",
            "source_hash": window.source_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    structured_ref = f"portara-cqg://sha256/{window.source_hash}"
    content_hash = research_evidence_content_hash(
        requirement_id="expectation_pricing",
        kind="market",
        authority="exchange",
        source_id=window.source_id,
        source_url=window.source_url,
        published_at=latest.observed_at,
        excerpt=excerpt,
        structured_payload_ref=structured_ref,
    )
    candidate = EvidenceCandidate(
        evidence_id=f"archive_{content_hash[:32]}",
        requirement_id="expectation_pricing",
        kind="market",
        authority="exchange",
        source_id=window.source_id,
        source_url=AnyUrl(window.source_url),
        published_at=latest.observed_at,
        observed_at=latest.observed_at,
        received_at=latest.observed_at + timedelta(seconds=1),
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=structured_ref,
        tool_call_id=f"archive:{case_id}:expectation_pricing",
        research_session_id=f"archive:{case_id}",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )
    facts = [
        _fact(
            candidate,
            field="level",
            value=point.level,
            unit="yield_percent",
            metric_family="macro.policy_expectation",
            instrument=window.instrument,
            venue=window.venue,
            event_offset=point.event_offset,
            observed_at=point.observed_at,
            source_hash=window.source_hash,
            event_id=case_id,
            source_timezone=window.source_timezone,
        )
        for point in window.points
    ]
    facts.append(
        _fact(
            candidate,
            field="delta",
            value=latest.delta,
            unit="bps",
            metric_family="macro.policy_expectation",
            instrument=window.instrument,
            venue=window.venue,
            event_offset="t-5m",
            observed_at=latest.observed_at,
            source_hash=window.source_hash,
            event_id=case_id,
            source_timezone=window.source_timezone,
            window_start_at=window.points[0].observed_at,
            window_end_at=latest.observed_at,
        )
    )
    return {
        "capability_id": "replay.research",
        "requirement_id": "expectation_pricing",
        "query_aliases": [
            f"{case_id}:expectation_pricing",
            f"Acquire typed pre-event rates repricing evidence for {case_id}",
        ],
        "evidence_candidates": [candidate.model_dump(mode="json")],
        "facts": [item.model_dump(mode="json") for item in facts],
    }


def cross_asset_fixture(case_id: str, window: PortaraCrossAssetEventWindow) -> dict[str, object]:
    observed_at = max(point.observed_at for point in window.points)
    values = {
        point.event_offset: {
            "level": point.level,
            "event_return": point.event_return,
        }
        for point in window.points
    }
    excerpt = json.dumps(
        {
            "archive": "Portara/CQG licensed ASCII",
            "case_id": case_id,
            "delivery_vendor": "portara-cqg",
            "instrument": window.instrument,
            "market_origin": window.venue,
            "source_timezone": window.source_timezone,
            "event_window": values,
            "reconstruction_mode": "reconstructed_history",
            "source_hash": window.source_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    structured_ref = f"portara-cqg://sha256/{window.source_hash}"
    content_hash = research_evidence_content_hash(
        requirement_id="cross_asset_confirmation",
        kind="market",
        authority="exchange",
        source_id=window.source_id,
        source_url=window.source_url,
        published_at=observed_at,
        excerpt=excerpt,
        structured_payload_ref=structured_ref,
    )
    candidate = EvidenceCandidate(
        evidence_id=f"archive_{content_hash[:32]}",
        requirement_id="cross_asset_confirmation",
        kind="market",
        authority="exchange",
        source_id=window.source_id,
        source_url=AnyUrl(window.source_url),
        published_at=observed_at,
        observed_at=observed_at,
        received_at=observed_at + timedelta(seconds=1),
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=structured_ref,
        tool_call_id=f"archive:{case_id}:cross_asset_confirmation",
        research_session_id=f"archive:{case_id}",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )
    facts = [
        _fact(
            candidate,
            field=field,
            value=value,
            unit=unit,
            metric_family=window.metric_family,
            instrument=window.instrument,
            venue=window.venue,
            event_offset=point.event_offset,
            observed_at=point.observed_at,
            source_hash=window.source_hash,
            event_id=case_id,
            source_timezone=window.source_timezone,
            window_start_at=(window.points[0].observed_at if field == "event_return" else None),
            window_end_at=(point.observed_at if field == "event_return" else None),
        )
        for point in window.points
        for field, value, unit in (
            ("level", point.level, "price"),
            ("event_return", point.event_return, "percent"),
        )
    ]
    return {
        "capability_id": "replay.research",
        "requirement_id": "cross_asset_confirmation",
        "query_aliases": [
            f"{case_id}:cross_asset_confirmation",
            f"Acquire typed ES event-window confirmation for {case_id}",
        ],
        "evidence_candidates": [candidate.model_dump(mode="json")],
        "facts": [item.model_dump(mode="json") for item in facts],
    }


def _fact(
    candidate: EvidenceCandidate,
    *,
    field: str,
    value: str,
    unit: str,
    metric_family: str,
    instrument: str,
    venue: str,
    event_offset: str,
    observed_at: datetime,
    source_hash: str,
    event_id: str | None = None,
    source_timezone: str | None = None,
    window_start_at: datetime | None = None,
    window_end_at: datetime | None = None,
) -> FactEnvelope:
    attributes: dict[str, str | bool | float | None] = {
        "archive_hash": source_hash,
        "reconstruction_mode": "reconstructed_history",
    }
    if source_timezone is not None:
        attributes["source_timezone"] = source_timezone
    if event_id is not None:
        attributes["event_id"] = event_id
    payload_schema_ref = "reconstructed-market-event-window.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=candidate.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=instrument,
        venue=venue,
        value=value,
        unit=unit,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
        event_offset=event_offset,
        published_at=observed_at,
        source_id=candidate.source_id,
        independence_group=candidate.source_id,
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash
        ),
        evidence_id=candidate.evidence_id,
        requirement_id=candidate.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=instrument,
        venue=venue,
        value=value,
        unit=unit,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
        event_offset=event_offset,
        observed_at=observed_at,
        received_at=observed_at + timedelta(seconds=1),
        published_at=observed_at,
        source_id=candidate.source_id,
        independence_group=candidate.source_id,
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--portara-us2y", type=Path)
    parser.add_argument("--portara-dxy", type=Path)
    parser.add_argument("--portara-es", type=Path)
    parser.add_argument("--portara-attestation", type=Path)
    parser.add_argument("--portara-timezone", default="America/Chicago")
    parser.add_argument("--require-full-hard-data", action="store_true")
    args = parser.parse_args()
    print(
        await build(
            args.source,
            args.target,
            portara_us2y_path=args.portara_us2y,
            portara_dxy_path=args.portara_dxy,
            portara_es_path=args.portara_es,
            portara_attestation_path=args.portara_attestation,
            portara_timezone=args.portara_timezone,
            require_full_hard_data=args.require_full_hard_data,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
