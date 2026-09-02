from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import EvaluationDatasetManifest
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.kernel.decision_hub_kernel.application.evolution import (
    dataset_manifest_hash,
)

DATASET = Path("packs/crypto_macro/evaluations/r2r_pit_v1/manifest.json")


def test_r2r_dataset_has_locked_distribution_and_pit_lineage() -> None:
    dataset = load_research_evaluation_dataset(DATASET)

    assert len(dataset.cases) == 12
    assert dataset.manifest.event_family_counts == {
        "central_bank_speech": 2,
        "monetary_policy_decision": 3,
        "inflation_release": 3,
        "labor_release": 2,
        "geopolitical_shock": 2,
    }
    assert len({case.case_id for case in dataset.cases}) == 12
    assert all(case.outcome_available_at is None for case in dataset.cases)
    assert all(not case.outcome_labels for case in dataset.cases)


def test_r2r_dataset_rejects_tampering(tmp_path: Path) -> None:
    copied = tmp_path / "dataset"
    shutil.copytree(DATASET.parent, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    case_path = copied / manifest["fixture_refs"][0]
    case_path.write_text(case_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="research_dataset_fixture_hash_mismatch"):
        load_research_evaluation_dataset(manifest_path)


def test_r2r_dataset_rejects_future_evidence_even_with_updated_hash(tmp_path: Path) -> None:
    copied = tmp_path / "dataset"
    shutil.copytree(DATASET.parent, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    case_path = copied / manifest["fixture_refs"][0]
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case["archived_capability_fixtures"][0]["evidence_candidates"][0][
        "received_at"
    ] = "2099-01-01T00:00:00Z"
    case_path.write_text(json.dumps(case, sort_keys=True), encoding="utf-8")

    # Hash repair alone must not bypass the independent PIT audit.
    from hashlib import sha256

    manifest["fixture_hashes"][manifest["fixture_refs"][0]] = sha256(
        case_path.read_bytes()
    ).hexdigest()
    model = EvaluationDatasetManifest.model_validate(manifest)
    manifest["manifest_hash"] = dataset_manifest_hash(
        model.split,
        model.fixture_refs,
        model.cutoff_rule,
        fixture_hashes=manifest["fixture_hashes"],
        label_rule=model.label_rule,
        leakage_audit=model.leakage_audit,
        authorization=model.authorization,
        visibility=model.visibility,
        source_mode=model.source_mode,
        window_start_at=model.window_start_at,
        window_end_at=model.window_end_at,
        event_family_counts=model.event_family_counts,
    )
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="research_dataset_pit_violation"):
        load_research_evaluation_dataset(manifest_path)
