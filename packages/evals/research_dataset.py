from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import (
    EvaluationDatasetManifest,
    ResearchEvaluationCase,
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


@dataclass(frozen=True)
class ResearchEvaluationDataset:
    manifest: EvaluationDatasetManifest
    cases: tuple[ResearchEvaluationCase, ...]
    case_paths: tuple[Path, ...]


def load_research_evaluation_dataset(manifest_path: Path) -> ResearchEvaluationDataset:
    try:
        manifest = EvaluationDatasetManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("research_dataset_manifest_invalid") from exc
    _validate_manifest_hash(manifest)
    if (
        manifest.split not in {"replay", "holdout"}
        or manifest.source_mode not in {"fixture", "reconstructed_fixture"}
        or manifest.leakage_audit != "passed"
    ):
        raise ValueError("research_dataset_manifest_invalid")
    _validate_data_coverage(manifest_path, manifest)

    cases: list[ResearchEvaluationCase] = []
    paths: list[Path] = []
    for reference in manifest.fixture_refs:
        relative = Path(reference)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("research_dataset_fixture_ref_invalid")
        path = manifest_path.parent / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError("research_dataset_fixture_missing") from exc
        if hashlib.sha256(raw).hexdigest() != manifest.fixture_hashes.get(reference):
            raise ValueError("research_dataset_fixture_hash_mismatch")
        try:
            case = ResearchEvaluationCase.model_validate_json(raw)
        except ValueError as exc:
            raise ValueError("research_dataset_case_invalid") from exc
        _validate_case(case, manifest)
        cases.append(case)
        paths.append(path)

    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("research_dataset_case_duplicate")
    family_counts = Counter(case.event_family for case in cases)
    if dict(family_counts) != manifest.event_family_counts:
        raise ValueError("research_dataset_family_mismatch")
    return ResearchEvaluationDataset(manifest, tuple(cases), tuple(paths))


def _validate_data_coverage(manifest_path: Path, manifest: EvaluationDatasetManifest) -> None:
    """Validate the optional coverage report emitted for reconstructed datasets.

    Older frozen datasets have a v1 source-hash file and remain valid. Newer
    builders add a relative report reference and hash; both are checked before
    the dataset can be used for an audit.
    """

    source_hashes_path = manifest_path.parent / "source_hashes.json"
    try:
        source_hashes = json.loads(source_hashes_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise ValueError("research_dataset_source_hashes_invalid") from exc
    if not isinstance(source_hashes, dict):
        raise ValueError("research_dataset_source_hashes_invalid")
    coverage_ref = source_hashes.get("coverage_report_ref")
    coverage_hash = source_hashes.get("coverage_report_hash")
    coverage_file_hash = source_hashes.get("coverage_report_file_hash")
    if coverage_ref is None and coverage_hash is None:
        return
    if not isinstance(coverage_ref, str) or not isinstance(coverage_hash, str):
        raise ValueError("research_dataset_coverage_reference_invalid")
    if coverage_file_hash is not None and not isinstance(coverage_file_hash, str):
        raise ValueError("research_dataset_coverage_reference_invalid")
    relative = Path(coverage_ref)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("research_dataset_coverage_reference_invalid")
    report_path = manifest_path.parent / relative
    try:
        raw = report_path.read_bytes()
    except OSError as exc:
        raise ValueError("research_dataset_coverage_missing") from exc
    if coverage_file_hash is not None and hashlib.sha256(raw).hexdigest() != coverage_file_hash:
        raise ValueError("research_dataset_coverage_hash_mismatch")
    try:
        report = json.loads(raw)
    except ValueError as exc:
        raise ValueError("research_dataset_coverage_invalid") from exc
    if not isinstance(report, dict):
        raise ValueError("research_dataset_coverage_invalid")
    if report.get("dataset_id") != manifest.dataset_id:
        raise ValueError("research_dataset_coverage_dataset_mismatch")
    report_hash = report.pop("report_hash", None)
    if not isinstance(report_hash, str):
        raise ValueError("research_dataset_coverage_invalid")
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    if report_hash != coverage_hash or hashlib.sha256(canonical).hexdigest() != report_hash:
        raise ValueError("research_dataset_coverage_hash_mismatch")


def _validate_manifest_hash(manifest: EvaluationDatasetManifest) -> None:
    expected = dataset_manifest_hash(
        manifest.split,
        manifest.fixture_refs,
        manifest.cutoff_rule,
        fixture_hashes=manifest.fixture_hashes,
        label_rule=manifest.label_rule,
        leakage_audit=manifest.leakage_audit,
        authorization=manifest.authorization,
        visibility=manifest.visibility,
        source_mode=manifest.source_mode,
        window_start_at=manifest.window_start_at,
        window_end_at=manifest.window_end_at,
        event_family_counts=manifest.event_family_counts,
    )
    if manifest.manifest_hash != expected:
        raise ValueError("research_dataset_manifest_hash_mismatch")


def _validate_case(
    case: ResearchEvaluationCase,
    manifest: EvaluationDatasetManifest,
) -> None:
    trigger = case.trigger_evidence
    if (trigger.published_at is not None and trigger.published_at > trigger.observed_at) or not (
        trigger.observed_at <= trigger.received_at <= case.cutoff_at
    ):
        raise ValueError("research_dataset_pit_violation")
    if not (
        manifest.window_start_at <= trigger.received_at <= manifest.window_end_at
        and manifest.window_start_at <= case.cutoff_at <= manifest.window_end_at
    ):
        raise ValueError("research_dataset_window_violation")

    requirements = {item.requirement_id: item for item in case.evidence_requirements}
    if len(requirements) != len(case.evidence_requirements):
        raise ValueError("research_dataset_requirement_duplicate")
    expected_hard = {
        item.requirement_id for item in case.evidence_requirements if item.importance == "hard"
    }
    if set(case.expected_hard_requirement_ids) != expected_hard:
        raise ValueError("research_dataset_hard_requirements_mismatch")

    fixture_keys: set[tuple[str, str]] = set()
    evidence_ids: set[str] = set()
    referenced_sources: set[str] = {
        str(trigger.source_url) if trigger.source_url is not None else ""
    }
    for fixture in case.archived_capability_fixtures:
        key = (fixture.capability_id, fixture.requirement_id)
        if key in fixture_keys:
            raise ValueError("research_dataset_capability_fixture_duplicate")
        fixture_keys.add(key)
        if fixture.requirement_id not in requirements:
            raise ValueError("research_dataset_requirement_missing")
        for candidate in fixture.evidence_candidates:
            if candidate.evidence_id in evidence_ids:
                raise ValueError("research_dataset_evidence_duplicate")
            evidence_ids.add(candidate.evidence_id)
            if candidate.requirement_id != fixture.requirement_id:
                raise ValueError("research_dataset_evidence_lineage_mismatch")
            if (
                candidate.published_at is not None
                and candidate.published_at > candidate.observed_at
            ) or not (candidate.observed_at <= candidate.received_at <= case.cutoff_at):
                raise ValueError("research_dataset_pit_violation")
            expected_hash = research_evidence_content_hash(
                requirement_id=candidate.requirement_id,
                kind=candidate.kind,
                authority=candidate.authority,
                source_id=candidate.source_id,
                source_url=str(candidate.source_url) if candidate.source_url else None,
                published_at=candidate.published_at,
                excerpt=candidate.excerpt,
                structured_payload_ref=candidate.structured_payload_ref,
            )
            if candidate.content_hash != expected_hash:
                raise ValueError("research_dataset_content_hash_mismatch")
            if candidate.source_url is not None:
                referenced_sources.add(str(candidate.source_url))
        fixture_evidence_ids = {candidate.evidence_id for candidate in fixture.evidence_candidates}
        fact_ids: set[str] = set()
        for fact in fixture.facts or []:
            if fact.fact_id in fact_ids:
                raise ValueError("research_dataset_fact_duplicate")
            fact_ids.add(fact.fact_id)
            if (
                fact.evidence_id not in fixture_evidence_ids
                or fact.requirement_id != fixture.requirement_id
            ):
                raise ValueError("research_dataset_fact_lineage_mismatch")
            if (
                (fact.published_at is not None and fact.published_at > fact.observed_at)
                or fact.observed_at > fact.received_at
                or fact.received_at > case.cutoff_at
                or (fact.window_end_at is not None and fact.window_end_at > fact.observed_at)
            ):
                raise ValueError("research_dataset_pit_violation")
            expected_payload_hash = research_fact_payload_hash(
                requirement_id=fact.requirement_id,
                metric_family=fact.metric_family,
                field=fact.field,
                instrument=fact.instrument,
                venue=fact.venue,
                value=fact.value,
                unit=fact.unit,
                window_start_at=fact.window_start_at,
                window_end_at=fact.window_end_at,
                event_offset=fact.event_offset,
                published_at=fact.published_at,
                source_id=fact.source_id,
                independence_group=fact.independence_group,
                delay_class=fact.delay_class,
                payload_schema_ref=fact.payload_schema_ref,
                attributes=fact.attributes,
            )
            if fact.payload_hash != expected_payload_hash:
                raise ValueError("research_dataset_fact_payload_hash_mismatch")
            if fact.fact_id != research_fact_instance_id(
                evidence_id=fact.evidence_id,
                payload_hash=fact.payload_hash,
            ):
                raise ValueError("research_dataset_fact_id_mismatch")

    if referenced_sources - set(case.source_refs):
        raise ValueError("research_dataset_source_ref_missing")
    if case.outcome_available_at is not None and case.outcome_available_at <= case.cutoff_at:
        raise ValueError("research_dataset_pit_violation")
    for label in case.outcome_labels:
        if label.available_at <= case.cutoff_at or label.source_ref not in case.source_refs:
            raise ValueError("research_dataset_pit_violation")
