"""Freeze research hypotheses without granting publication or trading authority."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from packages.contracts_py.decision_hub_contracts import ResearchStrategyReceipt
from packages.kernel.decision_hub_kernel.application.fact_lineage import digest, run_scope
from packages.kernel.decision_hub_kernel.application.market_scope import outcome_scope_enabled
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    ExecutionPermissionRecord,
    FactVersionRecord,
    PublicationDecisionRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunInputRefRecord,
    as_utc,
    utcnow,
)

HORIZON_SECONDS = {"30m": 1800, "24h": 86400, "72h": 259200, "7d": 604800}
_AS_OF_CLAIM = re.compile(
    r"(?:截至|as\s+of)\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))",
    re.IGNORECASE,
)


def future_as_of_claim(text: str, frozen_at: datetime) -> bool:
    try:
        return any(
            datetime.fromisoformat(match.replace("Z", "+00:00")) > frozen_at
            for match in _AS_OF_CLAIM.findall(text)
        )
    except ValueError:
        return True


def freeze_outlooks(
    session,
    *,
    run,
    artifact,
    result,
    gate,
    published_horizons,
    now,
    raw_result=None,
    native_review_verified=False,
    retrospective=False,
    trigger_evidence_ids=(),
    retain_only=False,
):
    """Called only in the same transaction that claims the Artifact publication slot."""
    if session.get(PublicationDecisionRecord, artifact.artifact_id):
        return
    result_hash = research_result_content_hash(result) if result else None
    session.add(
        PublicationDecisionRecord(
            artifact_id=artifact.artifact_id,
            run_id=run.run_id,
            status=gate.status.value,
            result_hash=result_hash,
            decisions_json=json.dumps(gate.decisions, ensure_ascii=False),
            published_horizons_json=json.dumps(
                [item.model_dump(mode="json") for item in published_horizons]
            ),
            review_refs_json=json.dumps(
                [
                    {
                        "review_id": review.review_id,
                        "result_hash": review.result_hash,
                        "resolution": review.resolution,
                        "version_matches": review.result_hash == result_hash,
                        "native_provenance_verified": native_review_verified,
                    }
                    for review in result.reviews or []
                ]
                if result
                else []
            ),
            decided_at=now,
        )
    )
    session.add(
        ExecutionPermissionRecord(
            artifact_id=artifact.artifact_id,
            allowed=False,
            policy_version="research-only.v1",
            reasons_json=json.dumps(
                ["execution_channel_not_configured"]
                + (["retrospective_projection"] if retrospective else [])
            ),
            decided_at=utcnow() if retrospective else now,
        )
    )
    if result is None or retain_only:
        return
    market, instrument = run_scope(session, run.run_id)
    evidence = {
        row.evidence_id: row
        for row in session.query(ResearchEvidenceRecord).filter_by(run_id=run.run_id).all()
    }
    facts = session.query(ResearchFactRecord).filter_by(run_id=run.run_id).all()
    refs = {
        ref.fact_id: ref.fact_version_id
        for ref in session.query(RunInputRefRecord).filter_by(run_id=run.run_id).all()
    }
    raw = raw_result or result
    raw_horizons = {item.horizon: item for item in raw.horizons}
    thesis = raw.causal_case.thesis if raw.causal_case else ""

    def add(
        *,
        family,
        method,
        horizon,
        action,
        probability,
        probability_status,
        evidence_ids,
        trigger,
        invalidation,
        payload,
        expires_at=None,
        target_instrument=instrument,
        extra_reasons=(),
    ):
        reasons = list(extra_reasons)
        selected = [evidence[id] for id in evidence_ids if id in evidence]
        accepted = [row for row in selected if row.quality == "accepted"]
        selected_facts = [
            fact
            for fact in facts
            if fact.evidence_id in evidence_ids and fact.quality == "accepted"
        ]
        versions = sorted({refs[fact.fact_id] for fact in selected_facts if fact.fact_id in refs})
        if market == "unknown" or target_instrument == "unknown":
            reasons.append("scope_unknown")
        if not outcome_scope_enabled(market, target_instrument):
            reasons.append("observation_market_not_enabled")
        if action == "no_trade":
            reasons.append("no_directional_hypothesis")
        if not accepted or not versions:
            reasons.append("accepted_versioned_inputs_missing")
        if any(as_utc(row.received_at) > now or as_utc(row.observed_at) > now for row in selected):
            reasons.append("input_after_freeze")
        for version_id in versions:
            version = session.get(FactVersionRecord, version_id)
            if version is None:
                reasons.append("fact_version_missing")
            elif as_utc(version.known_at) > now or as_utc(version.observed_at) > now:
                reasons.append("input_after_freeze")
        if any(id not in evidence and id not in trigger_evidence_ids for id in evidence_ids):
            # Trigger snapshots remain in the raw payload, but need a typed
            # accepted input too; an unexplained cross-Run citation is excluded.
            reasons.append("unresolved_evidence_reference")
        if expires_at is not None and expires_at <= now:
            reasons.append("model_hypothesis_expired")
        if retrospective:
            reasons.append("retrospective_projection")
        data_as_of = max((as_utc(row.received_at) for row in selected), default=None)
        outlook_id = "outlook_" + digest([run.run_id, method, horizon])[:40]
        session.add(
            AnalyticalOutlookRecord(
                outlook_id=outlook_id,
                run_id=run.run_id,
                artifact_id=artifact.artifact_id,
                study_id=run.study_id,
                market=market,
                instrument=target_instrument,
                method_family=family,
                method_version=method,
                horizon=horizon,
                action=action,
                subjective_probability=probability,
                probability_status=probability_status,
                data_as_of=data_as_of,
                window_start_at=now,
                window_end_at=now + timedelta(seconds=HORIZON_SECONDS[horizon]),
                result_hash=result_hash,
                evidence_refs_json=json.dumps(evidence_ids),
                fact_version_refs_json=json.dumps(versions),
                raw_payload_json=json.dumps(payload, ensure_ascii=False),
                trigger=trigger,
                invalidation=invalidation,
                cohort="retrospective" if retrospective else "prospective",
                eligibility="excluded" if reasons else "eligible",
                exclusion_reasons_json=json.dumps(sorted(set(reasons))),
                research_quality=result.final_coverage.status,
                recorded_at=utcnow() if retrospective else now,
            )
        )
        session.add(
            AnalyticalObservationRecord(
                outlook_id=outlook_id,
                status="excluded" if reasons else "pending",
                attempt=0,
                next_attempt_at=now + timedelta(seconds=HORIZON_SECONDS[horizon]),
            )
        )

    for horizon in result.horizons:
        original = raw_horizons.get(horizon.horizon, horizon)
        add(
            family="ai_synthesis",
            method=f"ai-synthesis.v1:{result.profile_ref}:{result.runtime_version}",
            horizon=horizon.horizon,
            action=horizon.action,
            probability=horizon.subjective_probability,
            probability_status=horizon.probability_status,
            evidence_ids=list(horizon.evidence_refs),
            trigger=horizon.trigger,
            invalidation=horizon.invalidation,
            expires_at=horizon.expires_at,
            payload={
                "horizon": original.model_dump(mode="json"),
                "thesis": thesis,
                "model_finished_at": raw.finished_at.isoformat(),
            },
            extra_reasons=(["future_as_of_claim"] if future_as_of_claim(thesis, now) else [])
            + (["model_finished_after_freeze"] if raw.finished_at > now else []),
        )

    seen = set()
    for fact in facts:
        if fact.quality != "accepted" or fact.capability_id != "data.historical":
            continue
        attributes = json.loads(fact.attributes_json)
        raw_receipt = attributes.get("daily_strategy_receipt_json")
        if not isinstance(raw_receipt, str):
            continue
        try:
            receipt = ResearchStrategyReceipt.model_validate_json(raw_receipt)
        except ValueError:
            continue
        if receipt.strategy_version in seen:
            continue
        seen.add(receipt.strategy_version)
        reasons = []
        if receipt.strategy_version != "daily-trend-v0.1":
            reasons.append("daily_method_not_audited")
        if receipt.parameters != {"short_window": 20, "long_window": 60}:
            reasons.append("daily_parameters_not_audited")
        try:
            if date.fromisoformat(receipt.as_of) >= now.date():
                reasons.append("daily_bar_not_closed")
        except ValueError:
            reasons.append("daily_as_of_invalid")
        if receipt.evidence_refs and receipt.evidence_refs != [fact.evidence_id]:
            reasons.append("daily_receipt_evidence_mismatch")
        add(
            family="daily_rule",
            method=receipt.strategy_version,
            horizon="7d",
            action={
                "bullish": "long",
                "bearish": "short",
                "range": "neutral",
                "unresolved": "no_trade",
            }[receipt.outlook],
            probability=None,
            probability_status="unavailable",
            evidence_ids=[fact.evidence_id],
            trigger=receipt.trigger,
            invalidation=receipt.invalidation,
            payload=receipt.model_dump(mode="json"),
            target_instrument=fact.instrument or "unknown",
            extra_reasons=reasons,
        )
