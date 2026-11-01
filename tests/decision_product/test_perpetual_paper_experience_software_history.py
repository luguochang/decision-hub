"""Software audit upgrades preserve real paper originals and semantic replay."""

import hashlib
import json
import os
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_software_only_upgrade_preserves_history_and_rejects_tampering(
    binding_case, monkeypatch, tmp_path
):
    from packages.kernel.decision_hub_kernel.application import (
        perpetual_paper_experience as experience,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_records import ActionOutcomeRecord
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.persistence.perpetual_execution_adapter import (
        PerpetualExecutionAdapter,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.perpetual_paper_experience import (
        METHOD,
        SCHEMA,
        experience_original,
    )
    from tests.decision_product.test_perpetual_manual_execution import normal_queue_lifecycle
    from tests.decision_product.test_perpetual_research_binding import OWNER

    case = binding_case
    wire, svc, clock, executor = normal_queue_lifecycle(case, monkeypatch, return_runtime=True)
    funding = PerpetualFundingHistorySource(case["db"], case["archive"])
    service = experience.PerpetualPaperExperienceService(
        case["db"], funding, clock=lambda: clock[0]
    )
    clock[0] += timedelta(seconds=2)

    async def funding_response(transport, request):
        if request.url.path == "/api/v5/public/time":
            rows = [{"ts": str(int(clock[0].timestamp() * 1000))}]
        else:
            assert request.url.path == "/api/v5/public/funding-rate-history"
            rows = []
        return httpx.Response(200, request=request, json={"code": "0", "data": rows})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", funding_response)
    svc.funding_history = funding
    completed = executor.process_due()
    assert completed["errors"] == [] and completed["funding_reconciled"] == 1
    account_key = wire["execution"]["intent"]["account_key"]
    with case["db"].session() as session:
        episode, episode_ref = PerpetualExecutionAdapter(session).episode(account_key)
        outcome = session.get(ActionOutcomeRecord, episode["outcome_id"])
        outcome_ref = {"ref": outcome.outcome_id, "hash": outcome.payload_hash}
        rows = session.scalars(
            select(RunRoleOutputRecord).where(RunRoleOutputRecord.schema_version == SCHEMA)
        ).all()
        assert len(rows) == 1  # Actual normal postcommit derivative, not seeded case data.
        original = service.read_in_session(
            session, {"ref": rows[0].output_id, "hash": rows[0].payload_hash}, cutoff_at=clock[0]
        )
        row_identity = (rows[0].output_id, rows[0].role, rows[0].created_at)
    evidence_path = Path(os.getenv("PAPER_SOFTWARE_HISTORY_EVIDENCE", tmp_path / "evidence.json"))
    evidence = {
        "classification": (
            "authored_real_temporary_paper_closure_not_native_or_financial_qualification"
        ),
        "case_ref": original.ref.model_dump(mode="json"),
        "case_body_json": original.body_json,
        "case_body_sha256": hashlib.sha256(original.body_json.encode()).hexdigest(),
        "database": str(case["db"].engine.url),
        "original_row_id": row_identity[0],
        "original_role": row_identity[1],
        "checks": [],
    }

    def save(check):
        evidence["checks"].append(check)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    save("normal_postcommit_original_pinned_before_software_change")
    software_paths = {
        (Path(experience.__file__).resolve().parents[1] / name).resolve()
        for name in original.body["software"]
    }

    class SoftwareUpgradePath(type(Path())):
        def read_bytes(self):
            raw = super().read_bytes()
            if self.resolve() in software_paths:
                return raw + b"\n# test-only harmless software fingerprint change\n"
            return raw

    # Only this module's creation-fingerprint reads vary. Source files, global
    # pathlib, raw Provider archives and all business readers remain unchanged.
    with monkeypatch.context() as upgrade:
        upgrade.setattr(experience, "Path", SoftwareUpgradePath)
        with case["db"].session() as session:
            historic = service.read_in_session(session, original.ref, cutoff_at=clock[0])
        assert historic == original and historic.body_json == original.body_json
        save("actual_reader_keeps_original_bytes_after_software_only_change")
        clock[0] += timedelta(seconds=1)
        prepared = service.prepare(episode_ref=episode_ref, outcome_ref=outcome_ref).value
        assert prepared.body["software"] != original.body["software"]
        evidence["changed_creation_software"] = prepared.body["software"]
        assert prepared.body["source_version_key"] == original.body["source_version_key"]
        assert prepared.body["calculation_method"] == METHOD
        assert prepared.body["calculation_method_hash"] == canonical_hash(METHOD)
        assert original.body["source_version_key"] == canonical_hash(
            [outcome_ref, canonical_hash(METHOD)]
        )
        assert service.record(episode_ref=episode_ref, outcome_ref=outcome_ref) == original
        assert service.record(episode_ref=episode_ref, outcome_ref=outcome_ref) == original
        assert service.process_due() == ()
        with case["db"].session() as session:
            rows = session.scalars(
                select(RunRoleOutputRecord).where(RunRoleOutputRecord.schema_version == SCHEMA)
            ).all()
            assert len(rows) == 1
            row = rows[0]
            assert (row.output_id, row.role, row.created_at) == row_identity
            assert row.payload_json == original.body_json and row.payload_hash == original.ref.hash
            selected = experience.paper_experience_context_in_session(
                session,
                owner_id=OWNER,
                account_kind="paper",
                account_id="manual-account",
                instrument_id=original.body["instrument_id"],
                profile_ref=original.body["profile_ref"],
                horizon="4h",
                cutoff_at=clock[0],
            )
            assert len(selected) == 1 and selected[0].content_hash == original.ref.hash
            assert selected[0].evidence_id == original.ref.ref
        save("first_metadata_time_identity_kept_record_twice_dedup_and_selector")

    def reject_body_change(label, mutate, *, rehash=True, reason):
        altered = deepcopy(original.body)
        mutate(altered)
        forged = experience_original(altered)
        with case["db"].session() as session:
            nested = session.begin_nested()
            row = session.get(RunRoleOutputRecord, original.ref.ref)
            row.payload_json = forged.body_json
            if rehash:
                row.payload_hash = forged.ref.hash
            session.flush()
            with pytest.raises(ValueError, match=reason):
                service.read_in_session(
                    session, forged.ref if rehash else original.ref, cutoff_at=clock[0]
                )
            nested.rollback()
        save(label)

    reject_body_change(
        "body_tamper_rejected_against_pinned_hash",
        lambda b: b.update(net_sign="flat"),
        rehash=False,
        reason="invalid_or_future",
    )
    for field, value in (("net_pnl_usdt", "999"), ("net_sign", "flat"), ("owner_id", "foreign")):
        reject_body_change(
            "rehashed_" + field + "_tamper_rejected",
            lambda b, k=field, v=value: b.update({k: v}),
            reason="original_changed",
        )

    def unknown_method(body):
        body["calculation_method"]["version"] = "paper-episode-realized-cashflow.v999"
        body["calculation_method_hash"] = canonical_hash(body["calculation_method"])

    reject_body_change("unknown_method_rejected", unknown_method, reason="method_unsupported")
    reject_body_change(
        "same_version_method_tamper_rejected",
        lambda b: b["calculation_method"].update(net="gross"),
        reason="method_unsupported",
    )
    reject_body_change(
        "malformed_creation_software_rejected",
        lambda b: b["software"].update({next(iter(b["software"])): "not-a-sha"}),
        reason="creation_software_invalid",
    )
    reject_body_change(
        "extra_creation_software_path_rejected",
        lambda b: b["software"].update({"foreign.py": "a" * 64}),
        reason="creation_software_invalid",
    )
    body = original.body
    assert body["reference_horizon"] == "4h"
    assert float(body["actual_holding_seconds"]) < 14400
    assert body["funding_cash_usdt"] == "0"
    assert "mark_risk_path_unavailable" in body["gaps"]
    assert all(
        body[field] is False
        for field in ("probability_scored", "financial_qualified", "skill_consumed")
    )
    capture = body["funding_coverage"]["pages"][0]["capture"]
    raw = case["archive"].root / capture["body_hash"][:2] / capture["body_hash"]
    saved_raw = raw.read_bytes()
    evidence["funding_raw_before_sha256"] = hashlib.sha256(saved_raw).hexdigest()
    try:
        raw.write_bytes(saved_raw + b" ")
        with (
            case["db"].session() as session,
            pytest.raises(ValueError, match="archive_corrupt|hash|raw"),
        ):
            service.read_in_session(session, original.ref, cutoff_at=clock[0])
        save("real_raw_funding_tamper_rejected")
    finally:
        raw.write_bytes(saved_raw)
    with case["db"].session() as session:
        final = service.read_in_session(session, original.ref, cutoff_at=clock[0])
    assert final == original and final.body_json == original.body_json
    evidence["funding_raw_after_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    assert evidence["funding_raw_before_sha256"] == evidence["funding_raw_after_sha256"]
    save("original_case_and_raw_bytes_restored_and_verified")
