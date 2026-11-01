"""Bounded exact-query reuse, executed inside the audited capability gateway."""

from __future__ import annotations

import json
from datetime import timedelta

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityResult
from packages.kernel.decision_hub_kernel.application.fact_lineage import digest, run_scope
from packages.kernel.decision_hub_kernel.application.fact_store import research_fact_instance_id
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    CapabilityReuseRecord,
    DshSessionLinkRecord,
    EvidenceParsedLinkRecord,
    ParsedVersionRecord,
    RawCaptureRecord,
    RunInputRefRecord,
    utcnow,
)
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE

PUBLIC_REUSABLE = frozenset(
    {
        "market.crypto_derivatives",
        "market.crypto_crowding",
        "market.cross_asset",
    }
)


class ArchivedResearchAdapter:
    def __init__(self, inner, database, archive, fingerprint, *, clock=utcnow):
        self.inner, self.database, self.archive = inner, database, archive
        self.fingerprint, self.clock = fingerprint, clock
        self.capability_id = inner.capability_id
        self.supported_modes = inner.supported_modes
        self.supported_fields = getattr(inner, "supported_fields", frozenset())

    def key(self, query):
        if (
            query.capability_id not in PUBLIC_REUSABLE
            or query.mode != "live"
            or query.event_at
            or query.requested_event_offsets
            or query.content_ref
            or query.evidence_ref
            or not query.symbols
        ):
            return None
        with self.database.session() as session:
            link = (
                session.query(DshSessionLinkRecord)
                .filter_by(dsh_session_id=query.research_session_id)
                .first()
            )
            if link is None:
                return None
            scope = run_scope(session, link.run_id)
        if "unknown" in scope:
            return None
        payload = query.model_dump(mode="json")
        for key in (
            "request_id",
            "research_session_id",
            "round",
            "observed_at",
            "requested_observed_at",
            "cutoff_at",
            "deadline_at",
        ):
            payload.pop(key, None)
        return digest(["exact-public-30s.v1", self.fingerprint, scope, payload])

    async def execute(self, query):
        batch = CURRENT_CAPTURE.get()
        key = self.key(query) if batch else None
        if batch:
            batch.reuse_key = key
        if key:
            reused = self._load(key, query, batch)
            if reused is not None:
                return reused
        return await self.inner.execute(query)

    def _load(self, key, query, batch):
        now = self.clock()
        with self.database.session() as session:
            row = (
                session.query(CapabilityReuseRecord)
                .filter(
                    CapabilityReuseRecord.query_key == key,
                    CapabilityReuseRecord.known_at <= min(now, query.cutoff_at),
                    CapabilityReuseRecord.expires_at >= now,
                )
                .order_by(CapabilityReuseRecord.known_at.desc())
                .first()
            )
            if row is None:
                return None
            result = ResearchCapabilityResult.model_validate_json(row.result_json)
            parsed_map, fact_map = (
                json.loads(row.parsed_versions_json),
                json.loads(row.fact_versions_json),
            )
            # Check every archived input before reuse. Missing/corrupt files cause
            # an ordinary provider fetch; no capability can silently use a stub.
            try:
                for parsed_id in set(parsed_map.values()):
                    parsed = session.get(ParsedVersionRecord, parsed_id)
                    if parsed is None or parsed.archive_status != "captured":
                        return None
                    captures = json.loads(parsed.capture_ids_json)
                    if not captures:
                        return None
                    for capture_id in captures:
                        capture = session.get(RawCaptureRecord, capture_id)
                        if capture is None or not capture.body_ref:
                            return None
                        self.archive.read(capture.body_ref)
            except (OSError, ValueError):
                return None
        evidence, facts, ids = [], [], {}
        for item in result.evidence_candidates:
            new_id = research_evidence_instance_id(
                content_hash=item.content_hash, research_session_id=query.research_session_id,
                content_ref=item.content_ref, acquisition_receipt_id=item.acquisition_receipt_id,
            )
            ids[item.evidence_id] = new_id
            batch.reused_evidence[new_id] = parsed_map[item.evidence_id]
            batch.reused_observed[new_id] = item.observed_at
            evidence.append(
                item.model_copy(
                    update={
                        "evidence_id": new_id,
                        "research_session_id": query.research_session_id,
                        "round": query.round,
                        "tool_call_id": query.request_id,
                        "received_at": now,
                    }
                )
            )
        for item in result.facts or []:
            new_id = research_fact_instance_id(
                evidence_id=ids[item.evidence_id], payload_hash=item.payload_hash
            )
            batch.reused_facts[new_id] = fact_map[item.fact_id]
            batch.reused_observed[new_id] = item.observed_at
            facts.append(
                item.model_copy(
                    update={
                        "fact_id": new_id,
                        "evidence_id": ids[item.evidence_id],
                        "received_at": now,
                    }
                )
            )
        return result.model_copy(
            update={
                "request_id": query.request_id,
                "evidence_candidates": evidence,
                "facts": facts,
                "completed_at": now,
                "cost_usd": 0.0,
                "provider_attempts": [],
                "provider": result.provider + ":reused",
            }
        )


def record_reusable_batch(session, *, run_id, result, batch):
    if (
        batch is None
        or not batch.reuse_key
        or batch.reused_facts
        or not result.facts
        or not result.evidence_candidates
        or any(item.attributes.get("derivation") for item in result.facts)
        or any(item.quality != "accepted" for item in [*result.evidence_candidates, *result.facts])
    ):
        return
    parsed_map, fact_map = {}, {}
    for evidence in result.evidence_candidates:
        link = session.get(EvidenceParsedLinkRecord, evidence.evidence_id)
        parsed = session.get(ParsedVersionRecord, link.parsed_version_id) if link else None
        if parsed is None or parsed.archive_status != "captured":
            return
        parsed_map[evidence.evidence_id] = parsed.parsed_version_id
    for fact in result.facts:
        ref = session.get(RunInputRefRecord, fact.fact_id)
        if ref is None:
            return
        fact_map[fact.fact_id] = ref.fact_version_id
    now = utcnow()
    # Expiration anchored to actual observation, never extended by reuse.
    expires = min(item.observed_at for item in result.evidence_candidates) + timedelta(seconds=30)
    if expires < now:
        return
    batch_id = "reuse_" + digest([run_id, result.request_id])[:48]
    if session.get(CapabilityReuseRecord, batch_id) is None:
        session.add(
            CapabilityReuseRecord(
                batch_id=batch_id,
                query_key=batch.reuse_key,
                run_id=run_id,
                result_json=result.model_dump_json(),
                fact_versions_json=json.dumps(fact_map),
                parsed_versions_json=json.dumps(parsed_map),
                known_at=now,
                expires_at=expires,
            )
        )
