"""Perpetual source behavior, using synthetic archived responses and no network."""
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
from packages.kernel.decision_hub_kernel.ports.okx_perpetual_original import (
    VERSION, RuleInputs, assemble_contract, decode_public_original, paper_dependency_gaps, public_url,
)

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
INST = "BTC-USDT-SWAP"


def metadata_row(inst=INST):
    base = inst.split("-")[0]
    return {
        "instType": "SWAP", "instId": inst, "ctType": "linear", "instFamily": base + "-USDT",
        "uly": base + "-USDT", "baseCcy": "", "quoteCcy": "", "settleCcy": "USDT",
        "ctVal": "0.01" if base == "BTC" else "0.1", "ctMult": "1", "ctValCcy": base,
        "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.1", "state": "live",
        "listTime": "1573557408000",
    }


def capture(kind, row, inst=INST):
    raw = json.dumps({"code": "0", "msg": "", "data": [row]}, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    original = CaptureOriginal(
        "raw_synthetic_" + kind, f"{VERSION}:{inst}:{kind}:" + "1" * 32,
        public_url(inst, kind), 200, f"raw://sha256/{digest}", digest, len(raw), VERSION, None,
        NOW - timedelta(seconds=1), NOW, NOW,
    )
    return original, raw


@pytest.mark.parametrize("inst", [INST, "ETH-USDT-SWAP"])
def test_real_swap_dimensions_do_not_invent_rules_or_account_values(inst):
    original, raw = capture("metadata", metadata_row(inst), inst)
    parsed = decode_public_original("metadata", inst, original, raw)
    contract = assemble_contract({"metadata": parsed}, RuleInputs(), cutoff=NOW)
    assert contract.inst_id == inst and contract.ct_mult == "1"
    assert contract.base == inst.split("-")[0] and contract.quote == "USDT"
    assert contract.effective_at is None and contract.effective_at_source == "unknown"
    assert contract.known_at == NOW and contract.margin_tier_ref is None
    assert contract.funding_schedule_ref is None
    assert paper_dependency_gaps(contract, as_of=NOW, has_fresh_executable_quote=False) == (
        "margin_tier_unknown", "fresh_executable_bid_ask_missing",
    )


def test_missing_contract_multiplier_is_not_assumed_one():
    original, raw = capture("metadata", {**metadata_row(), "ctMult": ""})
    with pytest.raises(ValueError, match="^perp_ctmult_required_no_default$"):
        decode_public_original("metadata", INST, original, raw)


@pytest.mark.parametrize("bad", ["spot_response", "spot_request", "body_tamper"])
def test_spot_alias_or_wrong_original_cannot_fill_perp(bad):
    row = metadata_row()
    if bad == "spot_response":
        row["instType"] = "SPOT"
    original, raw = capture("metadata", row)
    if bad == "body_tamper":
        raw = raw.replace(b'"tickSz":"0.1"', b'"tickSz":"0.2"')
    with pytest.raises(ValueError):
        decode_public_original("metadata", "BTC-USDT" if bad == "spot_request" else INST, original, raw)


def test_funding_indication_does_not_become_settled_cashflow_or_block_entry():
    next_time = NOW + timedelta(hours=3)
    original, raw = capture("funding", {
        "instId": INST, "fundingRate": "0.0001",
        "fundingTime": str(int(next_time.timestamp() * 1000)),
        "nextFundingTime": str(int((next_time + timedelta(hours=4)).timestamp() * 1000)),
        "ts": str(int(NOW.timestamp() * 1000)),
    })
    indication = decode_public_original("funding", INST, original, raw)
    assert indication.fields["scheduled_settlement_at"] == next_time
    assert indication.fields["settled_rate"] is None and indication.fields["cashflow_usdt"] is None
    metadata, raw_metadata = capture("metadata", metadata_row())
    contract = assemble_contract({
        "metadata": decode_public_original("metadata", INST, metadata, raw_metadata), "funding": indication,
    }, RuleInputs(), cutoff=NOW)
    assert contract.funding_schedule_ref is None
    assert "funding_schedule_unknown" not in paper_dependency_gaps(
        contract, as_of=NOW, has_fresh_executable_quote=True,
    )
    with pytest.raises(ValueError, match="^perp_source_clock_after_cutoff$"):
        assemble_contract({"metadata": decode_public_original("metadata", INST, metadata, raw_metadata),
                           "funding": replace(indication, observed_at=next_time)}, RuleInputs(), cutoff=NOW)


@pytest.fixture
def persisted_original(tmp_path, monkeypatch):
    from dataclasses import asdict
    from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import OKXPerpetualSource
    from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord, RunRecord
    from packages.provider_adapters.research.raw_capture import RawBodyArchive

    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database("sqlite:///" + (tmp_path / "original.sqlite3").as_posix())
    db.create_all()
    archive = RawBodyArchive(tmp_path / "raw")
    original, raw = capture("metadata", metadata_row())
    assert archive.put(raw) == (original.body_hash, original.body_ref)
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(original), media_type="application/json"))
        session.add(RunRecord(run_id="perp-test", event_id="test", status="running",
                              created_at=NOW, updated_at=NOW))
    source = OKXPerpetualSource(db, archive)
    try:
        yield db, archive, source, original
    finally:
        db.engine.dispose()


def require_live_test_run(session):
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
    if session.get(RunRecord, "perp-test", populate_existing=True).status != "running":
        raise ValueError("test_run_no_longer_running")


def save_contract(db, source, prepared):
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
    with db.session() as session:
        begin_immediate(session)
        return source.save_prepared_in_session(
            session, run_id="perp-test", prepared=prepared, writer_guard=require_live_test_run,
        )


def test_contract_survives_new_reader_and_detects_changed_archived_bytes(persisted_original):
    from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import OKXPerpetualSource
    db, archive, source, original = persisted_original
    prepared = source.prepare_contract(inst_id=INST, capture_ids={"metadata": original.capture_id}, cutoff=NOW)
    ref = save_contract(db, source, prepared)
    new_reader = OKXPerpetualSource(db, archive)
    with db.session() as session:
        contract = new_reader.read_contract_in_session(session, ref)
        assert contract == prepared.value.contract and contract.inst_id == INST
    digest = original.body_hash
    path = archive.root / digest[:2] / digest
    path.write_bytes(path.read_bytes().replace(b'"tickSz":"0.1"', b'"tickSz":"0.2"'))
    with db.session() as session, pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
        new_reader.read_contract_in_session(session, ref)


def test_changed_capture_or_run_after_prepare_leaves_no_contract(persisted_original):
    from sqlalchemy import func, select
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord, RunRoleOutputRecord, RunRecord
    db, _, source, original = persisted_original
    prepared = source.prepare_contract(inst_id=INST, capture_ids={"metadata": original.capture_id}, cutoff=NOW)
    with db.session() as session:
        session.get(RunRecord, "perp-test").status = "cancelled"
    with pytest.raises(ValueError, match="test_run_no_longer_running"):
        save_contract(db, source, prepared)
    with db.session() as session:
        session.get(RunRecord, "perp-test").status = "running"
        session.get(RawCaptureRecord, original.capture_id).byte_count += 1
    with pytest.raises(ValueError, match="original_preparation_readset_changed"):
        save_contract(db, source, prepared)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRoleOutputRecord)) == 0


def test_original_captured_after_cutoff_cannot_be_backdated(persisted_original):
    _, _, source, original = persisted_original
    with pytest.raises(ValueError, match="perp_original_not_known_at_cutoff"):
        source.prepare_contract(inst_id=INST, capture_ids={"metadata": original.capture_id},
                                cutoff=NOW - timedelta(microseconds=1))


@pytest.mark.parametrize("mutation", ["replace_prepared", "capture_map", "source_ref"])
def test_prepared_contract_cannot_be_replaced_or_mutated_before_save(persisted_original, mutation):
    from sqlalchemy import func, select
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    db, _, source, original = persisted_original
    prepared = source.prepare_contract(inst_id=INST, capture_ids={"metadata": original.capture_id}, cutoff=NOW)
    if mutation == "replace_prepared":
        prepared = replace(prepared, value=replace(prepared.value,
            contract=prepared.value.contract.model_copy(update={"ct_val": "1000"})))
    elif mutation == "capture_map":
        prepared.value.capture_ids["metadata"] = "different_capture"
    else:
        prepared.value.source_refs["metadata"] = prepared.value.source_refs["metadata"].model_copy(
            update={"hash": "0" * 64},
        )
    with pytest.raises(ValueError, match="(?:original_preparation_unissued|perp_prepared_value_changed)"):
        save_contract(db, source, prepared)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRoleOutputRecord)) == 0


def test_metadata_contract_reference_needs_no_research_run_or_new_business_row(persisted_original):
    from sqlalchemy import delete, func, select
    from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import OKXPerpetualSource
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord

    db, archive, source, original = persisted_original
    with db.session() as session:
        session.execute(delete(RunRecord))
    reference = source.metadata_contract_reference(original.capture_id)
    reader = OKXPerpetualSource(db, archive)
    with db.session() as session:
        contract = reader.read_contract_in_session(session, reference)
        assert contract.inst_id == INST and contract.ct_val == "0.01"
        assert contract.known_at == NOW and contract.effective_at is None
        assert contract.price_basis_refs.trade is None
        assert contract.margin_tier_ref is None and contract.funding_schedule_ref is None
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0
        assert session.scalar(select(func.count()).select_from(RunRoleOutputRecord)) == 0


def test_metadata_reference_authenticates_body_hash_and_original_capture(persisted_original):
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

    db, archive, source, original = persisted_original
    reference = source.metadata_contract_reference(original.capture_id)
    with db.session() as session, pytest.raises(ValueError, match="perp_metadata_contract_hash_mismatch"):
        source.read_contract_in_session(session, reference.model_copy(update={"hash": "0" * 64}))
    with db.session() as session:
        session.get(RawCaptureRecord, original.capture_id).recorded_at += timedelta(seconds=1)
    with db.session() as session, pytest.raises(ValueError, match="perp_metadata_contract_hash_mismatch"):
        source.read_contract_in_session(session, reference)
    digest = original.body_hash
    (archive.root / digest[:2] / digest).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
        source.metadata_contract_reference(original.capture_id)


def test_metadata_reference_rejects_wrong_source_and_missing_capture(persisted_original):
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

    db, _, source, original = persisted_original
    with pytest.raises(ValueError, match="perp_capture_missing"):
        source.metadata_contract_reference("not-a-capture")
    with db.session() as session:
        session.get(RawCaptureRecord, original.capture_id).context_id = original.context_id.replace(":metadata:", ":ticker:")
    with pytest.raises(ValueError, match="perp_metadata_capture_identity_invalid"):
        source.metadata_contract_reference(original.capture_id)
