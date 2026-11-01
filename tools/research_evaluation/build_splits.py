"""Freeze the six-case development and six-case holdout research datasets."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import ResearchEvaluationCase
from packages.kernel.decision_hub_kernel.application.evolution import dataset_manifest_hash

ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro" / "evaluations"
SOURCE = ROOT / "r2r_pit_v1"


def main() -> None:
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    cases = [
        ResearchEvaluationCase.model_validate_json(
            (SOURCE / ref).read_text(encoding="utf-8")
        )
        for ref in source_manifest["fixture_refs"]
    ]
    by_family: dict[str, list[ResearchEvaluationCase]] = defaultdict(list)
    for case in cases:
        by_family[case.event_family].append(case)
    development: list[ResearchEvaluationCase] = []
    holdout: list[ResearchEvaluationCase] = []
    for family in sorted(by_family):
        family_cases = sorted(by_family[family], key=lambda item: item.case_id)
        development.append(family_cases[0])
        holdout.extend(family_cases[1:])
    # One additional development case makes the intended 6/6 split while the
    # first case of every event family remains represented in both partitions.
    development.append(sorted(holdout, key=lambda item: item.case_id)[0])
    holdout = [item for item in holdout if item.case_id != development[-1].case_id]
    assert len(development) == len(holdout) == 6
    _write_split("r2r_dev_v1", "replay", "crypto_macro.r2r_dev.v1", development)
    _write_split("r2r_holdout_v1", "holdout", "crypto_macro.r2r_holdout.v1", holdout)


def _write_split(
    directory_name: str,
    split: str,
    dataset_id: str,
    cases: list[ResearchEvaluationCase],
) -> None:
    directory = ROOT / directory_name
    case_directory = directory / "cases"
    if directory.exists():
        shutil.rmtree(directory)
    case_directory.mkdir(parents=True)
    refs: list[str] = []
    fixture_hashes: dict[str, str] = {}
    for case in sorted(cases, key=lambda item: item.case_id):
        name = f"{case.case_id}.json"
        ref = f"cases/{name}"
        path = case_directory / name
        raw = case.model_dump_json(indent=2).encode("utf-8")
        path.write_bytes(raw)
        refs.append(ref)
        fixture_hashes[ref] = hashlib.sha256(raw).hexdigest()
    family_counts: dict[str, int] = defaultdict(int)
    for case in cases:
        family_counts[case.event_family] += 1
    window_start = min(case.trigger_evidence.received_at for case in cases)
    window_end = max(case.cutoff_at for case in cases)
    authorization = "owner:r2-r-06"
    cutoff_rule = "published_at <= observed_at <= received_at"
    label_rule = "outcomes.available_at > received_at"
    leakage_audit = "passed"
    source_mode = "fixture"
    visibility = "owner_only"
    event_family_counts = dict(sorted(family_counts.items()))
    base: dict[str, object] = {
        "authorization": authorization,
        "created_at": "2026-09-09T00:00:00Z",
        "cutoff_rule": cutoff_rule,
        "dataset_id": dataset_id,
        "event_family_counts": event_family_counts,
        "fixture_hashes": fixture_hashes,
        "fixture_refs": refs,
        "label_rule": label_rule,
        "leakage_audit": leakage_audit,
        "source_mode": source_mode,
        "split": split,
        "visibility": visibility,
        "window_start_at": window_start.isoformat(),
        "window_end_at": window_end.isoformat(),
    }
    base["manifest_hash"] = dataset_manifest_hash(
        split,
        refs,
        cutoff_rule,
        fixture_hashes=fixture_hashes,
        label_rule=label_rule,
        leakage_audit=leakage_audit,
        authorization=authorization,
        visibility=visibility,
        source_mode=source_mode,
        window_start_at=window_start,
        window_end_at=window_end,
        event_family_counts=event_family_counts,
    )
    (directory / "manifest.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "README.md").write_text(
        f"# {dataset_id}\n\nFrozen {split} split with six cases. "
        "The manifest and case hashes are immutable inputs for CV4/G1.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
