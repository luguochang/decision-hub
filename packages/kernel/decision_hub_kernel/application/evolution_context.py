"""Freeze complete input dispositions; send bounded, evidence-backed summaries."""

import hashlib
import json
from collections import Counter, defaultdict

from sqlalchemy.exc import IntegrityError

from packages.contracts_py.decision_hub_contracts.models import EvolutionPlanningContext
from packages.kernel.decision_hub_kernel.persistence.db import (
    ActivePointerRecord,
    CandidateVersionRecord,
    EvaluationRecord,
    EvolutionJobRecord,
    FailurePatternRecord,
    FeedbackRecord,
    RunRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    EvolutionContextSnapshotRecord as Snapshot,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


class EvolutionContextService:
    MAX_GROUPS = 32
    MAX_PROMPT_CHARACTERS = 20000

    def __init__(self, database):
        self.database = database

    def read(self, job_id):
        with self.database.session() as session:
            if session.get(EvolutionJobRecord, job_id) is None:
                raise KeyError(job_id)
            row = session.get(Snapshot, job_id)
            if row is None:
                return None
            body = json.loads(row.content_json)
            if _hash(body) != row.content_hash:
                raise ValueError("evolution_context_snapshot_corrupt")
            return body

    def build(self, job):
        body = self.read(job.job_id)
        binding = {
            "job_id": job.job_id,
            "domain_pack_ref": job.domain_pack_ref,
            "input_refs": job.input_refs,
        }
        if body is None:
            if job.candidate_id:
                raise ValueError("evolution_context_legacy_unrecorded")
            body = self._resolve(job, binding)
            try:
                with self.database.session() as session:
                    session.add(
                        Snapshot(
                            job_id=job.job_id,
                            content_hash=_hash(body),
                            content_json=_json(body),
                            created_at=utcnow(),
                        )
                    )
            except IntegrityError:
                body = self.read(job.job_id)
        if body["binding"] != binding:
            raise ValueError("evolution_context_input_changed")
        if body["admission"] != "ready":
            raise ValueError(body["admission"])
        return EvolutionPlanningContext.model_validate(body["context"])

    def _resolve(self, job, binding):
        items, summaries, evidence = [], [], []
        groups = defaultdict(list)
        seen_references = set()
        with self.database.session() as session:

            def resolve(reference, *, parent=None):
                item = {"reference": reference, "parent_reference": parent}
                items.append(item)
                if reference in seen_references:
                    item["disposition"] = "duplicate_reference"
                    return
                seen_references.add(reference)
                if reference.startswith("run:"):
                    row = session.get(RunRecord, reference[4:])
                    if row is None:
                        item["disposition"] = "missing_record"
                        return
                    data = {
                        key: getattr(row, key)
                        for key in (
                            "run_id",
                            "status",
                            "error_code",
                            "strategy_version",
                            "runtime_version",
                            "event_id",
                            "study_id",
                            "artifact_id",
                        )
                    }
                    data.update(
                        created_at=as_utc(row.created_at).isoformat(),
                        updated_at=as_utc(row.updated_at).isoformat(),
                    )
                    item["record"] = data
                    if row.status == "cancelled":
                        item["disposition"] = "cancelled_not_method_failure"
                    elif row.status not in {"failed", "degraded"} or not row.error_code:
                        item["disposition"] = "no_terminal_failure"
                    else:
                        item["disposition"] = "included_failure_group"
                        key = (
                            row.status,
                            row.error_code,
                            row.strategy_version,
                            row.runtime_version,
                        )
                        groups[key].append(item)
                    return
                if reference.startswith("feedback:"):
                    row = session.get(FeedbackRecord, reference.split(":", 1)[1])
                    if row is not None:
                        item.update(
                            disposition="included_feedback",
                            record={
                                "verdict": row.verdict,
                                "target_type": row.target_type,
                                "target_id": row.target_id,
                                "notes": row.notes,
                                "created_at": as_utc(row.created_at).isoformat(),
                            },
                        )
                        summaries.append(_json({"reference": reference, **item["record"]}))
                        evidence.append(reference)
                        return
                if reference.startswith("failure_pattern:"):
                    row = session.get(FailurePatternRecord, reference.split(":", 1)[1])
                    if row is not None:
                        refs = sorted(set(json.loads(row.source_refs_json)))
                        item.update(
                            disposition="expanded_pattern",
                            record={
                                "failure_code": row.failure_code,
                                "source_refs": refs,
                                "occurrence_count": row.occurrence_count,
                                "status": row.status,
                            },
                        )
                        for ref in refs:
                            if ref.startswith(("run:", "feedback:")):
                                resolve(ref, parent=reference)
                            else:
                                items.append(
                                    {
                                        "reference": ref,
                                        "parent_reference": reference,
                                        "disposition": "unsupported_reference",
                                    }
                                )
                        return
                if reference.startswith("evaluation:"):
                    row = session.get(EvaluationRecord, reference.split(":", 1)[1])
                    if row is not None:
                        item.update(
                            disposition="legacy_evaluation_not_validated",
                            record={
                                "evaluation_id": row.evaluation_id,
                                "forecast_id": row.forecast_id,
                                "label_status": row.label_status,
                            },
                        )
                        return
                item["disposition"] = "missing_or_unsupported_reference"

            for reference in sorted(job.input_refs):
                resolve(reference)
            for key, members in sorted(groups.items()):
                members.sort(key=lambda x: x["reference"])
                representative = members[0]["reference"]
                # The snapshot retains every member; the model cites the representative.
                evidence.append(representative)
                summaries.append(
                    _json(
                        {
                            "reference": representative,
                            "kind": "run_failure_group",
                            "status": key[0],
                            "error_code": key[1],
                            "strategy_version": key[2],
                            "recorded_runtime_version": key[3],
                            "runtime_identity_verified": False,
                            "run_count": len(members),
                            "members_hash": _hash([x["reference"] for x in members]),
                            "limitation": (
                                "Operational failures are not prediction quality scores. "
                                "Recorded runtime versions may be legacy defaults, "
                                "not executor proof."
                            ),
                        }
                    )
                )
                for item in members:
                    item["group_reference"] = representative
            pointer = (
                session.query(ActivePointerRecord)
                .filter_by(domain_pack_ref=job.domain_pack_ref)
                .one_or_none()
            )
            baseline = (
                session.get(CandidateVersionRecord, pointer.candidate_id) if pointer else None
            )
            if pointer and baseline is None:
                raise ValueError("evolution_active_candidate_not_found")
            context = EvolutionPlanningContext(
                job_id=job.job_id,
                domain_pack_ref=job.domain_pack_ref,
                context_items=summaries
                or ["No validated actionable input; do not invent a candidate."],
                evidence_refs=sorted(set(evidence)),
                active_candidate_id=baseline.candidate_id if baseline else None,
                active_candidate_version=baseline.version if baseline else None,
                active_candidate_content_hash=baseline.content_hash if baseline else None,
            )
        size = sum(len(x) for x in context.context_items) + sum(map(len, context.evidence_refs))
        admission = (
            "ready"
            if len(summaries) <= self.MAX_GROUPS and size <= self.MAX_PROMPT_CHARACTERS
            else "evolution_context_budget_exceeded"
        )
        return {
            "schema_version": "evolution-context.v2",
            "binding": binding,
            "frozen_at": utcnow().isoformat(),
            "admission": admission,
            "context": context.model_dump(mode="json"),
            "input_dispositions": items,
            "counts": dict(Counter(x["disposition"] for x in items)),
            "summary_count": len(summaries),
            "prompt_characters": size,
            "recovery_of": job.trigger_key.split(":")[1]
            if job.trigger_key.startswith("context-recovery:")
            else None,
        }
