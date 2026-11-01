from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchEvaluationCase,
)
from packages.contracts_py.decision_hub_contracts import (
    ResearchSourceRegistry as ResearchSourceRegistryContract,
)
from packages.evals.research_comparison import (
    SCORER_VERSION,
    evidence_for_scoring,
    facts_for_scoring,
)
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    AUTHORITY_RANK,
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.provider_adapters.research import (
    CryptoMacroFactPack,
    ReplayResearchCapabilityAdapter,
    ResearchSourceRegistry,
    ResearchSourceRegistryError,
    load_replay_archive,
)


class _AuditDatabase(Database):
    """Use the Kernel transaction API without touching its default data directory."""

    def __init__(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.create_all()


def _replay_manifest(
    pack_root: Path, bindings: bytes | None = None,
) -> ResearchCapabilityManifest:
    payload = yaml.safe_load(
        bindings if bindings is not None else (pack_root / "tools/bindings.yaml").read_bytes()
    )
    manifests = [
        ResearchCapabilityManifest.model_validate(item)
        for item in payload["capabilities"]
        if item["capability_id"] == "replay.research"
    ]
    if len(manifests) != 1:
        raise ValueError("research_replay_binding_ambiguous")
    return manifests[0]


def _source_admission(
    registry: ResearchSourceRegistry, candidate: EvidenceCandidate
) -> str:
    if candidate.source_url is None:
        raise ResearchSourceRegistryError(
            "research_source_url_missing", "archive has no source URL for registry admission"
        )
    policy = registry.require(
        str(candidate.source_url), usage="evidence", requirement_id=candidate.requirement_id
    )
    if AUTHORITY_RANK[candidate.authority] > AUTHORITY_RANK[policy.authority]:
        raise ResearchSourceRegistryError(
            "research_source_authority_mismatch", "archive authority exceeds its source policy"
        )
    return policy.source_ref


async def _audit_case(
    case: ResearchEvaluationCase,
    case_path: Path,
    manifest: ResearchCapabilityManifest,
    registry: ResearchSourceRegistry,
) -> dict[str, object]:
    archive = load_replay_archive(case_path)
    adapter = ReplayResearchCapabilityAdapter(
        capability_id=manifest.capability_id,
        fixtures=archive.queries,
        requirement_fixtures=archive.requirements,
    )
    gateway = ResearchCapabilityGatewayService(
        [manifest], [adapter], enabled_capabilities=[manifest.capability_id],
        clock=lambda: case.cutoff_at,
    )
    database = _AuditDatabase()
    try:
        return await _audit_case_in_database(case, gateway, registry, database)
    finally:
        database.engine.dispose()


async def _audit_case_in_database(
    case: ResearchEvaluationCase,
    gateway: ResearchCapabilityGatewayService,
    registry: ResearchSourceRegistry,
    database: Database,
) -> dict[str, object]:
    run_id = f"replay-audit:{case.case_id}"
    with database.session() as session:
        session.add(RunRecord(
            run_id=run_id, event_id=case.case_id, status="running",
            strategy_version="evaluation.replay-audit.v2", runtime_version="replay.research.v1",
            admission_origin="manual", created_at=case.cutoff_at, updated_at=case.cutoff_at,
        ))
    evidence: list[EvidenceCandidate] = []
    facts: list[FactEnvelope] = []
    retained: list[EvidenceCandidate] = []
    retained_facts: list[FactEnvelope] = []
    denials: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    source_refs: dict[str, str] = {}
    requirements = {item.requirement_id: item for item in case.evidence_requirements}
    evidence_service = ResearchEvidenceService(database)
    fact_store = ResearchFactStore(database)
    for fixture in case.archived_capability_fixtures:
        query = ResearchCapabilityQuery(
            schema_version="research-capability-query.v1",
            request_id=f"audit:{case.case_id}:{fixture.requirement_id}",
            capability_id=fixture.capability_id, requirement_id=fixture.requirement_id,
            query=fixture.query_aliases[0], target_url=None, symbols=[], fields=[],
            allowed_domains=[], max_results=20, max_cost_usd=0.0,
            research_session_id=f"audit:{case.case_id}", round=1, mode="replay",
            observed_at=case.cutoff_at, cutoff_at=case.cutoff_at,
        )
        try:
            result = await gateway.execute(query)
        except ResearchCapabilityError as exc:
            failures.append({
                "requirement_id": fixture.requirement_id, "error_code": exc.error_code,
            })
            continue
        evidence.extend(result.evidence_candidates)
        facts.extend(result.facts or [])
        permitted = []
        for candidate in result.evidence_candidates:
            try:
                source_refs[candidate.evidence_id] = _source_admission(registry, candidate)
            except ResearchSourceRegistryError as exc:
                denials.append({
                    "evidence_id": candidate.evidence_id,
                    "requirement_id": candidate.requirement_id,
                    "source_id": candidate.source_id, "source_url": str(candidate.source_url),
                    "error_code": exc.error_code,
                })
                continue
            permitted.append(candidate)
        permitted_ids = {item.evidence_id for item in permitted}
        permitted_facts = [item for item in result.facts or [] if item.evidence_id in permitted_ids]
        try:
            # Same atomic Kernel path as the durable gateway. Failed fact admission
            # rolls evidence back; the scorer never authorizes the raw candidates.
            with database.session() as session:
                admitted = evidence_service.accept_candidates_in_session(
                    session, run_id=run_id, capability_id=query.capability_id,
                    candidates=permitted, requirements=requirements,
                    cutoff_at=case.cutoff_at, facts=permitted_facts,
                )
                session.flush()
                admitted_facts = fact_store.accept_facts_in_session(
                    session, run_id=run_id, capability_id=query.capability_id,
                    facts=permitted_facts, cutoff_at=case.cutoff_at,
                )
        except (ValueError, ResearchCapabilityError) as exc:
            failures.append({
                "requirement_id": fixture.requirement_id,
                "error_code": getattr(exc, "error_code", str(exc)),
            })
            continue
        retained.extend(admitted)
        retained_facts.extend(admitted_facts)
    scored_evidence = evidence_for_scoring(case, retained, retained_facts)
    scored_facts = facts_for_scoring(retained_facts, scored_evidence)
    coverage = assess_evidence_sufficiency(
        case.evidence_requirements, scored_evidence, cutoff_at=case.cutoff_at,
        facts=scored_facts,
    )
    return {
        "case_id": case.case_id, "event_family": case.event_family,
        "cutoff_at": case.cutoff_at.isoformat(),
        "hard_coverage_ratio": coverage.hard_coverage_ratio,
        "soft_coverage_ratio": coverage.soft_coverage_ratio,
        "covered_requirement_ids": coverage.covered_requirement_ids,
        "hard_gaps": [
            {"requirement_id": item.requirement_id, "reason_code": item.reason_code}
            for item in coverage.gaps if item.importance == "hard"
        ],
        "soft_gaps": [
            {"requirement_id": item.requirement_id, "reason_code": item.reason_code}
            for item in coverage.gaps if item.importance == "soft"
        ],
        "evidence_count": len(evidence), "fact_count": len(facts),
        "fact_metric_families": sorted({item.metric_family for item in facts}),
        "sources": sorted({item.source_id for item in evidence}),
        "retained_evidence_count": len(retained), "retained_fact_count": len(retained_facts),
        "accepted_evidence_count": sum(item.quality == "accepted" for item in scored_evidence),
        "accepted_fact_count": sum(item.quality == "accepted" for item in scored_facts),
        "raw_evidence_quality_counts": dict(Counter(item.quality for item in evidence)),
        "source_denials": denials, "admission_failures": failures,
        "evidence_admission": [
            {
                "evidence_id": item.evidence_id, "requirement_id": item.requirement_id,
                "source_id": item.source_id, "source_ref": source_refs[item.evidence_id],
                "quality": item.quality, "freshness_status": item.freshness_status,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "observed_at": item.observed_at.isoformat(),
                "received_at": item.received_at.isoformat(), "content_hash": item.content_hash,
            }
            for item in scored_evidence
        ],
    }


async def audit(manifest_path: Path) -> dict[str, object]:
    dataset = load_research_evaluation_dataset(manifest_path)
    pack_root = manifest_path.resolve().parents[2]
    # Hash the exact policy bytes being enforced, rather than rereading mutable
    # files after admission and reporting a potentially different policy identity.
    policy_contents = {
        reference: (pack_root / reference).read_bytes()
        for reference in (
            "tools/bindings.yaml", "evidence/source_registry.yaml", "evidence/source_manifest.yaml",
        )
    }
    manifest = _replay_manifest(pack_root, policy_contents["tools/bindings.yaml"])
    registry = ResearchSourceRegistry(
        ResearchSourceRegistryContract.model_validate(
            yaml.safe_load(policy_contents["evidence/source_registry.yaml"])
        ),
        requirement_aliases=CryptoMacroFactPack.from_manifest(
            yaml.safe_load(policy_contents["evidence/source_manifest.yaml"])
        ).requirement_aliases,
    )
    reports = []
    for case, case_path in zip(dataset.cases, dataset.case_paths, strict=True):
        reports.append(await _audit_case(case, case_path, manifest, registry))
    return {
        "schema_version": "core-replay-audit.v2",
        "scorer_version": SCORER_VERSION,
        "dataset_id": dataset.manifest.dataset_id,
        "dataset_manifest_hash": dataset.manifest.manifest_hash,
        "source_mode": dataset.manifest.source_mode,
        "admission_path": "source_registry:evidence -> Kernel Evidence/Fact transaction -> scorer",
        "database_scope": "isolated_in_memory_per_case",
        "historical_availability_attested": False,
        "historical_source_policy_attested": False,
        "pit_scope": "declared_fixture_timestamps",
        "policy_files": {
            reference: hashlib.sha256(body).hexdigest()
            for reference, body in policy_contents.items()
        },
        "limitations": [
            "Frozen fixture hashes/PIT ordering do not prove actual source publication or receipt "
            "at the historical cutoff; reconstructed market timestamps are not live receipts.",
            "The unchanged on-disk Pack source registry is enforced and hashed; this is not "
            "attestation that the same source policy was frozen at the historical cutoff.",
            "Raw evidence/fact counts describe parsing only; denied sources never enter coverage.",
            "This offline audit does not attest release, promotion, deployment or live readiness.",
        ],
        "case_count": len(reports),
        "hard_coverage_mean": sum(
            float(item["hard_coverage_ratio"])
            for item in reports
            if isinstance(item["hard_coverage_ratio"], (int, float))
        )
        / len(reports),
        "soft_coverage_mean": sum(
            float(item["soft_coverage_ratio"])
            for item in reports
            if isinstance(item["soft_coverage_ratio"], (int, float))
        )
        / len(reports),
        "reports": reports,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = await audit(args.manifest)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
