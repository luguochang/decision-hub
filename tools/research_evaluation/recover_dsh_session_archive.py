from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from packages.contracts_py.decision_hub_contracts import (
    ResearchEvaluationCase,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.evals.research_comparison import build_case_report
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.kernel.decision_hub_kernel.application.research_review import (
    independent_review_receipt_hash,
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.runtime_adapters.dsh_runtime import load_dsh_session_archive
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result


@dataclass
class _ArchivedDshRuntime:
    runtime_id: str
    runtime_version: str
    profile_ref: str

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        del request, trace_sink
        raise RuntimeError("archived runtime is read-only")

    async def close(self) -> None:
        return None


def recover(
    *,
    archive_path: Path,
    manifest_path: Path,
    case_id: str,
    output_path: Path,
    runtime_id: str,
    runtime_version: str,
    profile_ref: str,
    expected_session_id: str | None = None,
) -> dict[str, object]:
    archive = load_dsh_session_archive(
        archive_path,
        expected_session_id=expected_session_id,
    )
    request = archive.canonical_request()
    dataset = load_research_evaluation_dataset(manifest_path)
    case = next((item for item in dataset.cases if item.case_id == case_id), None)
    if case is None:
        raise ValueError("archive_readback_case_missing")
    _validate_case_identity(request, case)

    traces = map_notifications(
        archive.notifications,
        run_id=request.run_id,
        research_session_id=archive.session_id,
        received_at=archive.finished_at,
    )
    result = map_session_result(
        archive.as_sdk_run(),
        request,
        traces,
        runtime_version=runtime_version,
        profile_ref=profile_ref,
        started_at=archive.created_at,
        finished_at=archive.finished_at,
    )
    status = cast(
        Literal["completed", "degraded", "failed", "timeout"], result.status
    )
    runtime = _ArchivedDshRuntime(runtime_id, runtime_version, profile_ref)
    latency_ms = round((archive.finished_at - archive.created_at).total_seconds() * 1000)
    report = build_case_report(
        dataset.manifest.dataset_id,
        case,
        runtime,
        result,
        status,
        None,
        archive.created_at,
        archive.finished_at,
        latency_ms,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_bytes = (report.model_dump_json(indent=2) + "\n").encode()
    output_path.write_bytes(report_bytes)

    reviews = result.reviews or []
    review = (
        reviews[0]
        if len(reviews) == 1 and reviews[0].role == "independent_reviewer"
        else None
    )
    receipt_hash = (
        independent_review_receipt_hash(
            role="independent_reviewer",
            target_claim=review.target_claim,
            evidence_refs=review.evidence_refs,
            challenge=review.challenge,
            resolution=review.resolution,
            limitations=review.limitations,
        )
        if review is not None
        else None
    )
    provenance: dict[str, object] = {
        "schema_version": "dsh-session-readback-provenance.v1",
        "dataset_id": dataset.manifest.dataset_id,
        "dataset_manifest_hash": dataset.manifest.manifest_hash,
        "case_id": case.case_id,
        "session_id": archive.session_id,
        "request_id": request.request_id,
        "archive_ref": _display_path(archive_path),
        "archive_sha256": archive.archive_sha256,
        "event_count": len(archive.events) + 1,
        "notification_count": len(archive.notifications),
        "finish_reason": archive.finish_reason,
        "trace_count": len(traces),
        "trace_hash": result.trace_hash,
        "evidence_count": len(result.evidence_candidates),
        "fact_count": len(result.facts or []),
        "review_count": len(reviews),
        "review_id": review.review_id if review is not None else None,
        "review_receipt_hash": receipt_hash,
        "result_content_hash": research_result_content_hash(result),
        "review_result_hash": review.result_hash if review is not None else None,
        "case_report_result_hash": report.result_hash,
        "case_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "runtime_id": runtime_id,
        "runtime_version": runtime_version,
        "profile_ref": profile_ref,
    }
    provenance["provenance_hash"] = _canonical_hash(provenance)
    provenance_path = output_path.with_suffix(".provenance.json")
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return provenance


def _validate_case_identity(
    request: ResearchSessionRequest, case: ResearchEvaluationCase
) -> None:
    expected_capabilities = sorted(
        {item.capability_id for item in case.archived_capability_fixtures}
    )
    trigger_matches = (
        len(request.input_evidence) == 1
        and request.input_evidence[0].model_dump(mode="json")
        == case.trigger_evidence.model_dump(mode="json")
    )
    if (
        request.execution_mode != "replay"
        or request.event_id != case.case_id
        or request.pit_cutoff_at != case.cutoff_at
        or request.evidence_requirements != case.evidence_requirements
        or request.evidence_refs != [case.trigger_evidence.evidence_id]
        or not trigger_matches
        or sorted(request.allowed_capabilities) != expected_capabilities
    ):
        raise ValueError("archive_readback_case_identity_mismatch")


def _canonical_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild a DSH evaluation case from its authoritative Session archive."
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-id", default="dsh-web")
    parser.add_argument("--runtime-version", required=True)
    parser.add_argument("--profile-ref", required=True)
    parser.add_argument("--expected-session-id")
    args = parser.parse_args()
    provenance = recover(
        archive_path=args.archive,
        manifest_path=args.manifest,
        case_id=args.case_id,
        output_path=args.output,
        runtime_id=args.runtime_id,
        runtime_version=args.runtime_version,
        profile_ref=args.profile_ref,
        expected_session_id=args.expected_session_id,
    )
    print(json.dumps(provenance, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
