"""Frozen deadlines through real Entry/claim/Host/writer; temporary SQLite only."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tests.decision_product import test_perpetual_action_publication as publication
from tests.decision_product.test_okx_perpetual_original import persisted_original as persisted_original
from tests.decision_product.test_perpetual_research_binding import AT, OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

POLICY = "saved_preferences_current_paper_ledger.v1"
METHOD = "49e4ab99ae4a86616fbd335316ccc9d9a78b26e3c5ed168ff3df97e707011d03"


def _paper(case):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import DecisionSettingsCommandV1, DecisionSettingsPartitionV1
    from packages.kernel.decision_hub_kernel.application.decision_settings import DecisionSettingsService
    from packages.kernel.decision_hub_kernel.application.decision_settings_binding import DecisionSettingsBindingReader
    from packages.kernel.decision_hub_kernel.application.perpetual_paper import CreatePaperAccount, PerpetualPaperService
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    paper = PerpetualPaperService(case["db"], case["source"], now=lambda: AT)
    old = case["settings"]
    paper.create_account(OWNER, CreatePaperAccount(request_id="deadline-paper", account_id="deadline-account", instrument_refs=(old.values.instrument,), profile=old.values.execution_profile, initial_cash_usdt=Decimal("2500"), simulation_acknowledged=True, execution_mode="manual_after_visible"))
    body = deepcopy(old.model_dump(mode="json"))
    body.update(request_id="deadline-settings", expected_state_version=0)
    body["partition"].update(account_kind="paper", account_id="deadline-account")
    partition = DecisionSettingsPartitionV1.model_validate(body["partition"])
    with case["db"].session() as session:
        account, position = paper.account_reader(session, OWNER, partition)
    body["values"].update(account_snapshot=account.model_dump(mode="json"), position_context=position.model_dump(mode="json"))
    body["values"]["utility_policy"].update(account_ref=account.original.model_dump(mode="json"), position_scenario="flat")
    utility = body["values"]["utility_policy"]
    utility["hash"] = canonical_hash({k: v for k, v in utility.items() if k != "hash"})
    settings = DecisionSettingsCommandV1.model_validate(body)
    saved = DecisionSettingsService(case["db"], now=lambda: AT, binding_reader=DecisionSettingsBindingReader(case["source"], now=lambda: AT, paper_account_reader=paper.account_reader))
    saved.save(OWNER, settings)
    case.update(settings=settings, settings_service=saved)


def _periodic(case, clock):
    from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import PerpetualResearchAdmissionDrivers
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import RiskResearchAdmissionService
    from packages.runtime_adapters.perpetual_research_entry import configured_perpetual_research_services
    from tests.decision_product.test_perpetual_research_schedule import body

    configured = configured_perpetual_research_services(case["db"], source=case["source"], now=lambda: clock[0])
    driver = PerpetualResearchAdmissionDrivers(case["db"], configured.entry, now=lambda: clock[0])
    consent = driver.periodic.save(OWNER, {**body(case), "schema_version": "perpetual-research-schedule-command.v2", "horizon": "4h", "input_policy": POLICY})
    engine = RiskResearchAdmissionService(case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver)
    return configured, driver, engine, consent


def _collect(case, clock, entry, prefix):
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import CollectedPerpetualCloseWindow
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import minute_end
    from tests.decision_product.test_perpetual_forecast_experience import future_pages

    ids = future_pages(case, minute_end(clock[0]), prefix=prefix, count=1500)
    value = CollectedPerpetualCloseWindow(ids, clock[0], clock[0])
    async def collect(*, inst_id):
        assert inst_id == "BTC-USDT-SWAP"
        return value
    entry.collect_window = collect


def test_expired_trigger_prepare_and_atomic_commit_still_reject(binding_case):
    from tests.decision_product.test_perpetual_research_entry import counts

    case = binding_case
    _paper(case)
    clock = [AT]
    configured, driver, engine, _ = _periodic(case, clock)
    slot = AT.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    clock[0] = slot + timedelta(minutes=59, seconds=10)
    driver.discover()
    claim = engine._claim()
    assert claim is not None
    _collect(case, clock, configured.entry, "expiry_")
    prepared = asyncio.run(driver.prepare(claim[0], claim[2], engine.owner_id))
    admission = prepared[2].inputs.value["admission"]
    assert admission["schema_version"] == "perpetual-research-admission.v3"
    assert datetime.fromisoformat(admission["valid_until"]) == clock[0] + timedelta(seconds=5400)
    before = counts(case)
    clock[0] = slot + timedelta(hours=1)
    with pytest.raises(ValueError, match="slot_expired"):
        driver.admit(claim[0], claim[2], engine, prepared)
    assert counts(case) == before
    with pytest.raises(ValueError, match="slot_expired"):
        asyncio.run(driver.prepare(claim[0], claim[2], engine.owner_id))
    assert counts(case) == before
    print("DEADLINE_PHASE expired_prepare_commit_atomic_rollback", flush=True)


def test_delayed_slot_reaches_real_host_fullwriter_after_trigger_expiry(binding_case, monkeypatch, tmp_path, record_property):
    from packages.kernel.decision_hub_kernel.application import perpetual_research_binding as binding
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import RiskResearchRequestRecord
    from packages.runtime_adapters.dsh_runtime.perpetual_profile import _root_material_view
    from tests.decision_product import test_perpetual_native_graph as native_graph
    from tests.decision_product import test_perpetual_research_binding as fixture_binding

    case = binding_case
    _paper(case)
    clock = [AT]
    configured, driver, engine, _ = _periodic(case, clock)
    slot = AT.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    prepared_at = slot + timedelta(minutes=10, seconds=10)
    clock[0] = prepared_at
    driver.discover()
    claim = engine._claim()
    assert claim is not None
    _collect(case, clock, configured.entry, "delayed_")
    prepared = asyncio.run(driver.prepare(claim[0], claim[2], engine.owner_id))
    initial = prepared[2].inputs.value["admission"]
    assert initial["schema_version"] == "perpetual-research-admission.v3"
    assert datetime.fromisoformat(initial["valid_until"]) == prepared_at + timedelta(seconds=5400)
    clock[0] += timedelta(seconds=5)
    admitted_at = clock[0]
    assert driver.admit(claim[0], claim[2], engine, prepared) is True
    with case["db"].session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        run = session.get(RunRecord, row.run_id)
        admission, admission_ref = binding.read_perpetual_admission_identity_in_session(session, run.run_id)
    assert datetime.fromisoformat(admission["runtime_deadline_at"]) == admitted_at + timedelta(seconds=3600)
    assert admission["automatic_source"]["source"]["deadline_at"] == (slot + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    assert admission["deadline_policy_ref"]["hash"]
    from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import authenticate_perpetual_deadlines
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    assert authenticate_perpetual_deadlines(admission) == admission["allowed_domain_policy"]["deadline_policy"]
    for field in ("valid_until", "runtime_deadline_at"):
        changed = deepcopy(admission)
        changed[field] = (datetime.fromisoformat(changed[field]) + timedelta(seconds=1)).isoformat()
        with pytest.raises(ValueError, match="deadline_original_mismatch"):
            authenticate_perpetual_deadlines(changed)
    changed = deepcopy(admission)
    changed["allowed_domain_policy"]["deadline_policy"]["operation_margin_seconds"] = 1801
    changed["deadline_policy_ref"]["hash"] = canonical_hash(changed["allowed_domain_policy"]["deadline_policy"])
    with pytest.raises(ValueError):
        authenticate_perpetual_deadlines(changed)
    changed = deepcopy(admission)
    changed["deadline_policy_ref"]["hash"] = "0" * 64
    with pytest.raises(ValueError, match="deadline_original_mismatch"):
        authenticate_perpetual_deadlines(changed)
    actual = {**case, "run_id": run.run_id, "event_id": run.event_id}
    finished_at = slot + timedelta(hours=1, minutes=5)
    clock[0] = finished_at
    for module in (fixture_binding, native_graph, publication):
        monkeypatch.setattr(module, "AT", finished_at)

    def request_setup(current, factory, service):
        factory.clock = lambda: admitted_at
        service.now = lambda: admitted_at
        request = factory.build(current["event_id"], current["run_id"])
        assert request.deadline_at == admitted_at + timedelta(seconds=3600)
        assert request.execution_budget.total_deadline_seconds == 3600
        assert request.execution_mode == "live"
        context, materials = native_graph.prepare(current, request)
        assert materials["allowed_domain_policy"]["deadline_policy"]["operation_margin_seconds"] == 1800
        assert materials["method_code_hash"] == METHOD
        return request, context, materials

    value = native_graph.build_perpetual_native_graph_case(actual, tmp_path, monkeypatch, phased=True, request_setup=request_setup, packet_builder=publication.lawful_packet_for, before_graph=lambda data: publication.authenticate_provider(data, monkeypatch), execution_mode="live")
    sizes = {"material_utf8": len(binding.canonical(_root_material_view(value["materials"])).encode()), "catalog_utf8": len(binding.canonical(value["materials"]["review_originals"]).encode()), "prompt_chars": len(value["execution"].prompt.prompt)}
    assert sizes["material_utf8"] <= 256000 and sizes["catalog_utf8"] <= 384000 and sizes["prompt_chars"] <= 262144
    record_property("complete_input_sizes", json.dumps(sizes))
    published, advice = publication.assert_actual_report_then_unique_perpetual_action(value, monkeypatch)
    assert published.response["status"] == "published"
    assert datetime.fromisoformat(advice["revision"]["valid_until"]) == prepared_at + timedelta(seconds=5400)
    assert datetime.fromisoformat(advice["target"]["window_start"]) > finished_at
    from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import PerpetualSavedReportQuery
    report = PerpetualSavedReportQuery(case["db"], now=lambda: finished_at + timedelta(seconds=41)).read(OWNER, run.run_id)
    assert report["run_id"] == run.run_id
    with case["db"].session() as session:
        reopened, reopened_ref = binding.read_perpetual_admission_identity_in_session(session, run.run_id)
    assert reopened == admission and reopened_ref == admission_ref
    from packages.kernel.decision_hub_kernel.application import perpetual_research_horizon as deadlines
    with monkeypatch.context() as patch:
        patch.setattr(deadlines, "perpetual_deadline_policy_original", lambda: (_ for _ in ()).throw(AssertionError("today policy must not be read")))
        with case["db"].session() as session:
            binding._authenticate_admission(session, reopened, purpose="frozen_read")
    print("DEADLINE_PHASE delayed_slot_Host_Graph_fullwriter_advice_report", json.dumps(sizes), flush=True)


def test_legacy_frozen_deadlines_and_method_identity_are_preserved(binding_case, tmp_path):
    from packages.kernel.decision_hub_kernel.application import perpetual_research_binding as binding
    from tests.decision_product.test_perpetual_research_binding import freeze

    case = binding_case
    service, reference = freeze(case)
    with case["db"].session() as session:
        original, reopened_ref = binding._read(session, case["run_id"], binding.ADMISSION_ROLE)
        wire = binding.canonical(original)
        frozen = binding._authenticate_admission(session, original, purpose="frozen_read")
    assert original["schema_version"] == "perpetual-research-admission.v1"
    assert reference.model_dump(mode="json") == reopened_ref
    assert binding.canonical(frozen) == wire
    assert binding.comparison_method_original()[2] == METHOD
    from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import authenticate_perpetual_deadlines
    assert authenticate_perpetual_deadlines(original) is None
    # V2's existing deadline equality is not silently given V3 policy semantics.
    assert authenticate_perpetual_deadlines({**original, "schema_version": "perpetual-research-admission.v2"}) is None
    assert binding.canonical(original) == wire
    from packages.kernel.decision_hub_kernel.decision.publication_anchor import freeze_publication_anchor
    with pytest.raises(ValueError, match="publication_anchor_candidate_expired"):
        freeze_publication_anchor(candidate_target=original["candidate_target"], anchor_rule=original["anchor_rule"], candidate_target_ref=original["candidate_target_ref"], anchor_rule_ref=original["anchor_rule_ref"], assigned_at=datetime.fromisoformat(original["valid_until"]), opportunity_id=original["opportunity_id"], commit_id="expired-legacy")
    print("DEADLINE_PHASE legacy_frozen_original_and_method_preserved", flush=True)

    # New manual current-ledger commands also carry an independent runtime.
    _paper(case)
    from tests.decision_product.test_perpetual_research_entry import client_for, command
    from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import parse_perpetual_research_command
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import CollectedPerpetualCloseWindow
    from packages.kernel.decision_hub_kernel.application.perpetual_research_schedule import authenticate_automatic_admission_in_session
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
    from tests.decision_product.test_perpetual_research_binding import factory_for

    clock = [AT]
    _, entry, _ = client_for(case, clock=lambda: clock[0])
    new_command = parse_perpetual_research_command({**command(case), "request_id": "deadline-manual-v3", "schema_version": "perpetual-research-command.v3", "input_policy": POLICY})
    prepared = entry.prepare(OWNER, new_command, CollectedPerpetualCloseWindow(case["ids"], AT, AT))
    clock[0] += timedelta(seconds=5)
    accepted = entry.commit(OWNER, new_command, prepared)
    with case["db"].session() as session:
        manual_admission = entry.binding.read_admission_in_session(session, accepted.run_id)
        assert manual_admission["schema_version"] == "perpetual-research-admission.v3"
        assert manual_admission["runtime_started_at"] == clock[0].isoformat(timespec="microseconds").replace("+00:00", "Z")
        assert datetime.fromisoformat(manual_admission["runtime_deadline_at"]) == clock[0] + timedelta(seconds=3600)
        assert datetime.fromisoformat(manual_admission["valid_until"]) == AT + timedelta(seconds=5400)
        authenticate_automatic_admission_in_session(session, manual_admission)
        # A Run declaring automatic origin cannot use the manual no-proof branch.
        record = session.get(RunRecord, accepted.run_id)
        record.admission_origin = "automatic"
        session.flush()
        with pytest.raises(ValueError, match="automatic_authority_required"):
            authenticate_automatic_admission_in_session(session, manual_admission)
        session.rollback()
    with case["db"].session() as session:
        with pytest.raises(ValueError, match="automatic_authority_required"):
            authenticate_automatic_admission_in_session(session, {**original, "runtime_started_at": AT.isoformat(), "runtime_deadline_at": (AT + timedelta(seconds=3600)).isoformat()})
    actual = {**case, "run_id": accepted.run_id, "event_id": accepted.event_id}
    factory, reader = factory_for(actual, tmp_path)
    factory.clock = reader.now = lambda: clock[0]
    request = factory.build(accepted.event_id, accepted.run_id)
    assert request.deadline_at == clock[0] + timedelta(seconds=3600)
    assert request.execution_budget.total_deadline_seconds == 3600
    assert entry.receipt(OWNER, new_command.request_id) == accepted
    print("DEADLINE_PHASE new_manual_v3_binding_factory_runtime_and_missing_auto_proof", flush=True)


def test_frozen_v3_automatic_runtime_ignores_today_runtime_constant(binding_case, monkeypatch):
    from packages.kernel.decision_hub_kernel.application import perpetual_research_binding as binding
    from packages.kernel.decision_hub_kernel.application import perpetual_research_schedule as schedule
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import RiskResearchRequestRecord

    case = binding_case
    _paper(case)
    clock = [AT]
    configured, driver, engine, _ = _periodic(case, clock)
    clock[0] = AT.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, minutes=10, seconds=10)
    driver.discover()
    claim = engine._claim()
    assert claim is not None
    _collect(case, clock, configured.entry, "frozen_policy_")
    prepared = asyncio.run(driver.prepare(claim[0], claim[2], engine.owner_id))
    assert driver.admit(claim[0], claim[2], engine, prepared) is True
    with case["db"].session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        run = session.get(RunRecord, row.run_id)
        admission, reference = binding.read_perpetual_admission_identity_in_session(session, run.run_id)
        assert admission["allowed_domain_policy"]["deadline_policy"]["runtime_seconds"] == 3600
        original = binding.canonical(admission)
    with monkeypatch.context() as patch:
        patch.setattr(schedule, "PERPETUAL_RESEARCH_RUNTIME_SECONDS", 7200)
        with case["db"].session() as session:
            schedule.authenticate_automatic_admission_in_session(session, admission)
            reopened = binding._authenticate_admission(session, admission, purpose="frozen_read")
            assert binding.canonical(reopened) == original
            assert binding._read(session, run.run_id, binding.ADMISSION_ROLE)[1] == reference
    print("DEADLINE_PHASE frozen_v3_automatic_policy_independent_of_today_runtime", flush=True)
