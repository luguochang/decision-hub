from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts import (
    EvaluationDatasetManifest,
    EvidenceCandidate,
    EvidenceRequirement,
    ResearchEvaluationCapabilityFixture,
    ResearchEvaluationCase,
    ResearchInputEvidence,
)
from packages.kernel.decision_hub_kernel.application.evolution import (
    dataset_manifest_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.provider_adapters.research import CryptoMacroFactPack

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs/crypto_macro"
DATASET_ROOT = PACK_ROOT / "evaluations/r2r_pit_v1"
CATALOG_PATH = DATASET_ROOT / "catalog.yaml"


class CatalogCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    event_family: Literal[
        "central_bank_speech",
        "monetary_policy_decision",
        "inflation_release",
        "labor_release",
        "geopolitical_shock",
    ]
    published_at: AwareDatetime
    source_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    authority: Literal["official", "verified_web"] = "official"
    kind: Literal["official", "web"] = "official"
    input_text: str = Field(min_length=1)
    event_excerpt: str = Field(min_length=1)
    delta_excerpt: str = Field(min_length=1)


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["research-evaluation-catalog.v1"]
    dataset_id: str = Field(min_length=1)
    created_at: AwareDatetime
    cases: list[CatalogCase] = Field(min_length=1)


def build() -> EvaluationDatasetManifest:
    catalog = Catalog.model_validate(_yaml(CATALOG_PATH))
    requirements = _requirements()
    hard_ids = [item.requirement_id for item in requirements if item.importance == "hard"]
    case_dir = DATASET_ROOT / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    fixture_refs: list[str] = []
    cutoffs: list[AwareDatetime] = []
    received_times: list[AwareDatetime] = []
    family_counts: dict[str, int] = {}

    for source in catalog.cases:
        observed_at = source.published_at + timedelta(seconds=1)
        received_at = source.published_at + timedelta(seconds=2)
        cutoff_at = source.published_at + timedelta(minutes=5)
        trigger = ResearchInputEvidence.model_validate(
            {
                "evidence_id": f"trigger_{source.case_id}",
                "kind": "transcript",
                "authority": "unverified",
                "source_id": "evaluation-catalog",
                "source_url": source.source_url,
                "published_at": source.published_at,
                "observed_at": source.published_at,
                "received_at": received_at,
                "content_hash": hashlib.sha256(
                    source.input_text.encode("utf-8")
                ).hexdigest(),
                "excerpt": source.input_text,
            }
        )
        fixtures = [
            _capability_fixture(
                source,
                requirement_id="event_identity",
                excerpt=source.event_excerpt,
                observed_at=observed_at,
                received_at=received_at,
            ),
            _capability_fixture(
                source,
                requirement_id="policy_or_data_delta",
                excerpt=source.delta_excerpt,
                observed_at=observed_at,
                received_at=received_at,
            ),
        ]
        case = ResearchEvaluationCase(
            schema_version="research-evaluation-case.v1",
            case_id=source.case_id,
            event_family=source.event_family,
            input_text=source.input_text,
            trigger_evidence=trigger,
            evidence_requirements=requirements,
            expected_hard_requirement_ids=hard_ids,
            archived_capability_fixtures=fixtures,
            cutoff_at=cutoff_at,
            outcome_available_at=None,
            outcome_labels=[],
            source_refs=[source.source_url],
        )
        relative = f"cases/{source.case_id}.json"
        (DATASET_ROOT / relative).write_text(
            json.dumps(
                case.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        fixture_refs.append(relative)
        cutoffs.append(cutoff_at)
        received_times.append(received_at)
        family_counts[source.event_family] = family_counts.get(source.event_family, 0) + 1

    fixture_hashes = {
        reference: hashlib.sha256((DATASET_ROOT / reference).read_bytes()).hexdigest()
        for reference in fixture_refs
    }
    cutoff_rule = "published_at <= observed_at <= received_at"
    manifest_hash = dataset_manifest_hash(
        "replay",
        fixture_refs,
        cutoff_rule,
        fixture_hashes=fixture_hashes,
        window_start_at=min(received_times),
        window_end_at=max(cutoffs),
        event_family_counts=family_counts,
        authorization="owner:r2-r-06",
    )
    manifest = EvaluationDatasetManifest(
        dataset_id=catalog.dataset_id,
        split="replay",
        manifest_hash=manifest_hash,
        fixture_refs=fixture_refs,
        fixture_hashes=fixture_hashes,
        cutoff_rule=cutoff_rule,
        label_rule="outcomes.available_at > received_at",
        leakage_audit="passed",
        authorization="owner:r2-r-06",
        visibility="owner_only",
        source_mode="fixture",
        window_start_at=min(received_times),
        window_end_at=max(cutoffs),
        event_family_counts=family_counts,
        created_at=catalog.created_at,
    )
    (DATASET_ROOT / "manifest.json").write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _capability_fixture(
    source: CatalogCase,
    *,
    requirement_id: str,
    excerpt: str,
    observed_at: AwareDatetime,
    received_at: AwareDatetime,
) -> ResearchEvaluationCapabilityFixture:
    content_hash = research_evidence_content_hash(
        requirement_id=requirement_id,
        kind=source.kind,
        authority=source.authority,
        source_id=source.source_id,
        source_url=source.source_url,
        published_at=source.published_at,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    candidate = EvidenceCandidate.model_validate(
        {
            "evidence_id": f"archive_{content_hash[:32]}",
            "requirement_id": requirement_id,
            "kind": source.kind,
            "authority": source.authority,
            "source_id": source.source_id,
            "source_url": source.source_url,
            "published_at": source.published_at,
            "observed_at": observed_at,
            "received_at": received_at,
            "content_hash": content_hash,
            "excerpt": excerpt,
            "structured_payload_ref": None,
            "tool_call_id": f"archive:{source.case_id}:{requirement_id}",
            "research_session_id": f"archive:{source.case_id}",
            "round": 1,
            "quality": "candidate",
            "freshness_status": "unknown",
            "conflict_group": None,
        }
    )
    return ResearchEvaluationCapabilityFixture(
        capability_id="replay.research",
        requirement_id=requirement_id,
        query_aliases=[
            f"{source.case_id}:{requirement_id}",
            f"Verify {requirement_id.replace('_', ' ')} for {source.case_id}",
        ],
        evidence_candidates=[candidate],
    )


def _requirements() -> list[EvidenceRequirement]:
    return list(CryptoMacroFactPack.from_pack(PACK_ROOT).all_contract_requirements())


def _yaml(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid_yaml_object:{path}")
    return payload


def main() -> int:
    manifest = build()
    print(
        json.dumps(
            {
                "dataset_id": manifest.dataset_id,
                "fixtures": len(manifest.fixture_refs),
                "manifest_hash": manifest.manifest_hash,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
