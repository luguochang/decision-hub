"""Replay settled manual paper originals as separate, bounded historical evidence."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import exists, func, select

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.action_execution_records import ExecutionEpisodeRecord, ExecutionFillRecord
from ..persistence.action_records import ActionOutcomeRecord
from ..persistence.action_repository import ActionRepository
from ..persistence.db import RunRoleOutputRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.perpetual_execution_adapter import PerpetualExecutionAdapter
from ..persistence.perpetual_paper_repository import PerpetualPaperRepository, _arithmetic
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_manual_execution import ManualExecutionCommand
from ..ports.perpetual_paper_experience import (
    KIND,
    METHOD,
    ROLE,
    SCHEMA,
    SOFTWARE_FILES,
    SOURCE,
    cashflow_label,
    experience_original,
    validate_calculation_method,
    validate_creation_software,
)
from .okx_perpetual_source import OKXPerpetualOriginalReader, OKXPerpetualSource
from .perpetual_forecast_experience import _publication
from .perpetual_funding_history_source import PerpetualFundingHistorySource
from .perpetual_research_binding import canonical, read_perpetual_admission_identity_in_session


def _at(value):
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _ref(value):
    return DecisionSourceRefV1.model_validate(value).model_dump(mode="json")


def _episode(session, reference):
    ref = _ref(reference)
    try:
        identity, version = ref["ref"].rsplit(":", 1)
        row = session.get(ExecutionEpisodeRecord, (identity, int(version)), populate_existing=True)
    except (ValueError, TypeError) as error:
        raise ValueError("paper_experience_episode_reference_invalid") from error
    if row is None:
        raise ValueError("paper_experience_episode_missing")
    envelope, body = PerpetualExecutionAdapter.artifact(row, "perpetual-manual-episode.v1")
    if (
        {"ref": envelope.id, "hash": envelope.payload_hash} != ref
        or row.episode_id != body["episode_id"]
        or row.version != body["version"]
        or row.portfolio_id != envelope.account_key
        or envelope.id != body["episode_id"] + ":" + str(body["version"])
        or PerpetualExecutionAdapter(session).intent(envelope.intent_id).command_json
        != body["command_json"]
    ):
        raise ValueError("paper_experience_episode_original_invalid")
    return envelope, body


def _creation_software():
    root = Path(__file__).resolve().parents[1]
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in SOFTWARE_FILES}


@_arithmetic()
def _derive(
    session, *, episode_ref, outcome_ref, funding_source, recorded_at, creation_software=None
):
    # All reads follow fixed originals. Neither current account nor advice heads
    # decide whether an old completed observation remains historical evidence.
    from .perpetual_execution_protocol import PerpetualPaperProtocolService
    from .perpetual_manual_execution import PerpetualManualPaperService

    at = aware_utc(recorded_at)
    episode_envelope, episode = _episode(session, episode_ref)
    if (
        episode["remaining_contracts"] != "0"
        or episode.get("funding_status") != "complete_zero_settlements"
    ):
        raise ValueError("paper_experience_settled_closed_episode_required")
    adapter = PerpetualExecutionAdapter(session)
    entry = adapter.intent(episode["entry_intent_id"])
    PerpetualManualPaperService._authority(session, entry)
    command = ManualExecutionCommand.model_validate_json(entry.command_json)
    repo = ActionRepository(session)
    confirmation = repo.get_command(entry.owner_id, entry.command_request_id)
    revision = repo.get_revision(entry.revision.ref)
    snapshot = repo.get_snapshot(revision.decision_id)
    admission, publication, published = _publication(session, snapshot.run_id, at)
    partition = command.partition.model_dump(mode="json")
    if (
        partition["account_kind"] != "paper"
        or admission["partition"] != partition
        or admission["owner_id"] != entry.owner_id
        or episode["owner_id"] != entry.owner_id
        or episode["account_id"] != entry.account_id
        or episode["command_json"] != entry.command_json
        or publication["revision"] != revision.model_dump(mode="json")
        or {"ref": revision.revision_id, "hash": revision.payload_hash} != _ref(entry.revision)
        or episode_envelope.account_key != entry.account_key
    ):
        raise ValueError("paper_experience_origin_scope_mismatch")
    outcome_ref = _ref(outcome_ref)
    outcome_row = session.get(ActionOutcomeRecord, outcome_ref["ref"], populate_existing=True)
    if outcome_row is None:
        raise ValueError("paper_experience_outcome_missing")
    envelope, outcome = adapter.artifact(outcome_row, "perpetual-manual-outcome.v1")
    outcome_intent = adapter.intent(envelope.intent_id)
    if (
        {"ref": envelope.id, "hash": envelope.payload_hash} != outcome_ref
        or outcome_row.outcome_id != outcome["id"]
        or outcome["id"] != episode["outcome_id"]
        or outcome_row.mode != "paper"
        or outcome_row.status != "settled"
        or outcome["status"] != "settled"
        or type(outcome.get("version")) is not int
        or outcome["version"] < 2
        or outcome_row.version != outcome["version"]
        or outcome_row.plan_id != entry.plan_id
        or outcome_row.revision_id != entry.revision.ref
        or outcome_row.opportunity_id != revision.opportunity_id
        or outcome["opportunity_id"] != revision.opportunity_id
        or outcome_row.protocol_hash != entry.protocol_ref.hash
        or outcome["episode_id"] != episode["episode_id"]
        or envelope.account_key != entry.account_key
        or outcome_intent.command_json != entry.command_json
        or outcome_intent.revision != entry.revision
        or outcome_intent.plan_id != entry.plan_id
        or outcome_intent.account_key != entry.account_key
        or outcome_intent.owner_id != entry.owner_id
        or outcome_intent.protocol_ref != entry.protocol_ref
        or outcome["ledger_revision"] != episode["ledger_revision"]
        or outcome["funding_coverage_ref"] != episode["funding_coverage_ref"]
    ):
        raise ValueError("paper_experience_outcome_scope_or_state_invalid")
    ledger_repo = PerpetualPaperRepository(session)
    initial = ledger_repo.revision(episode["initial_ledger_revision"]["ref"])
    final = ledger_repo.revision(episode["ledger_revision"]["ref"])
    if (
        initial["revision"] != episode["initial_ledger_revision"]
        or final["revision"] != episode["ledger_revision"]
        or any(value["account_key"] != entry.account_key for value in (initial, final))
        or not initial["funding_complete"]
        or not final["funding_complete"]
        or any(
            Decimal(row["signed_contracts"])
            for value in (initial, final)
            for row in value["positions"].values()
        )
        or final["event"].get("kind") != "paper_funding_reconciled"
        or final["parent"] is None
        or final["event"].get("funding_cash_usdt") != "0"
        or final["profile"] != admission["context"]["profile_ref"]
    ):
        raise ValueError("paper_experience_ledger_scope_or_funding_invalid")
    trade_final = ledger_repo.revision(final["parent"]["ref"])
    lower, upper = PerpetualManualPaperService._provider_holding_interval(
        initial, trade_final, ledger_repo
    )
    cursor, fills, gross, fees, quantity = trade_final, [], Decimal(0), Decimal(0), Decimal(0)
    while cursor["revision"] != initial["revision"]:
        event = cursor["event"]
        if event.get("kind") != "paper_fill" or cursor["parent"] is None:
            raise ValueError("paper_experience_unexplained_ledger_event")
        matching = session.scalars(
            select(ExecutionFillRecord).where(
                ExecutionFillRecord.portfolio_id == entry.account_key,
                ExecutionFillRecord.episode_id == episode["episode_id"],
            )
        ).all()
        found = []
        for row in matching:
            fill_envelope, fill = adapter.artifact(row, "perpetual-manual-fill.v1")
            if fill["ledger_revision"] == cursor["revision"]:
                found.append((row, fill_envelope, fill))
        if len(found) != 1:
            raise ValueError("paper_experience_exact_fill_missing")
        row, fill_envelope, fill = found[0]
        intent = adapter.intent(fill_envelope.intent_id)
        if (
            row.fill_id != fill["id"]
            or row.intent_id != intent.id
            or row.portfolio_id != entry.account_key
            or fill_envelope.account_key != entry.account_key
            or row.episode_id != episode["episode_id"]
            or intent.command_json != entry.command_json
            or intent.owner_id != entry.owner_id
            or intent.account_key != entry.account_key
            or intent.protocol_ref != entry.protocol_ref
            or intent.revision != entry.revision
            or fill["episode_id"] != episode["episode_id"]
            or fill["ledger_parent"] != cursor["parent"]
            or intent.phase != event["phase"]
            or fill["order_side"] != event["order_side"]
            or fill["filled_contracts"] != event["filled_contracts"]
            or fill["fill_price_usdt"] != event["fill_price"]
            or fill["trade_fee_usdt"] != event["trade_fee_usdt"]
            or fill["gross_pnl_usdt"] != event["gross_pnl_usdt"]
            or as_utc(row.executed_at) != _at(cursor["created_at"])
        ):
            raise ValueError("paper_experience_fill_ledger_mismatch")
        if intent.phase != "entry":
            prior_envelope, prior_episode = _episode(session, intent.episode_ref)
            trigger = repo.get_command("system:perp-paper:v1", intent.trigger_ref.ref)
            if (
                trigger is None
                or trigger.receipt_hash != intent.trigger_ref.hash
                or trigger.operation != "perp_exit_trigger"
                or trigger.response["episode_ref"] != _ref(intent.episode_ref)
                or trigger.response["entry_intent_id"] != entry.id
                or prior_episode["entry_intent_id"] != entry.id
            ):
                raise ValueError("paper_experience_exit_authority_invalid")
        gross += Decimal(fill["gross_pnl_usdt"])
        fees += Decimal(fill["trade_fee_usdt"])
        quantity += Decimal(fill["filled_contracts"]) * (1 if fill["order_side"] == "buy" else -1)
        fills.append(
            dict(
                ref={"ref": fill_envelope.id, "hash": fill_envelope.payload_hash},
                body=fill,
                phase=intent.phase,
                executed_at=cursor["created_at"],
                ledger_event=event,
            )
        )
        cursor = ledger_repo.revision(cursor["parent"]["ref"])
    fills.reverse()
    if (
        quantity
        or not fills
        or fills[0]["phase"] != "entry"
        or fills[-1]["ref"] != episode["last_fill_ref"]
    ):
        raise ValueError("paper_experience_fill_quantity_not_closed")
    coverage_receipt = repo.get_command(
        "system:perp-paper:v1", episode["funding_coverage_ref"]["ref"]
    )
    if (
        coverage_receipt is None
        or coverage_receipt.receipt_hash != episode["funding_coverage_ref"]["hash"]
        or coverage_receipt.operation != "perp_funding_coverage"
        or coverage_receipt.plan_id != entry.plan_id
    ):
        raise ValueError("paper_experience_funding_original_missing")
    response = coverage_receipt.response
    before_envelope, before_episode = _episode(session, response["episode_ref"])
    coverage = funding_source.read_coverage_body_in_session(
        session, body=response["coverage"], as_of=at
    )
    if (
        response["schema_version"] != "perpetual-funding-coverage-receipt.v1"
        or coverage.reference.model_dump(mode="json") != response["coverage_ref"]
        or coverage_receipt.payload_hash != coverage.reference.hash
        or not coverage.coverage_complete
        or coverage.settlements
        or coverage.gaps
        or coverage.instrument_id != partition["instrument_id"]
        or coverage.provider_lower != lower
        or coverage.provider_upper != upper
        or before_episode["episode_id"] != episode["episode_id"]
        or before_episode["ledger_revision"] != trade_final["revision"]
        or before_episode["entry_intent_id"] != entry.id
        or before_episode["remaining_contracts"] != "0"
        or final["event"].get("coverage_ref") != episode["funding_coverage_ref"]
    ):
        raise ValueError("paper_experience_zero_settlement_coverage_required")
    source = OKXPerpetualOriginalReader(funding_source.archive)
    contract = source.read_contract_in_session(
        session,
        DecisionSourceRefV1.model_validate(final["instrument_refs"][partition["instrument_id"]]),
    )
    # The existing protocol reader authenticates registration and frozen account
    # inputs. Its construction needs no mutable account or provider operation.
    protocol_service = PerpetualPaperProtocolService(
        funding_source.database, OKXPerpetualSource(funding_source.database, funding_source.archive)
    )
    protocol = protocol_service.read_in_session(session, entry.protocol_ref)
    if (
        protocol.schema_version != "perp-manual-paper-protocol.v1"
        or protocol.owner_id != entry.owner_id
        or protocol.account_id != entry.account_id
        or protocol.profile.model_dump(mode="json") != final["profile"]
    ):
        raise ValueError("paper_experience_protocol_scope_mismatch")
    from ..decision.perpetual_accounting import (
        linear_base_quantity,
        linear_gross_pnl_usdt,
        trade_fee_usdt,
    )
    from .perpetual_paper import PerpetualPaperService

    spec = PerpetualPaperService._spec(contract)
    instrument = partition["instrument_id"]
    for item in fills:
        fill, event = item["body"], item["ledger_event"]
        parent = ledger_repo.revision(fill["ledger_parent"]["ref"])
        child = ledger_repo.revision(fill["ledger_revision"]["ref"])
        held = Decimal(parent["positions"][instrument]["signed_contracts"])
        size, price = Decimal(fill["filled_contracts"]), Decimal(fill["fill_price_usdt"])
        expected_gross = (
            Decimal(0)
            if item["phase"] == "entry"
            else linear_gross_pnl_usdt(
                spec,
                side="long" if held > 0 else "short",
                contracts=size,
                entry_fill_price=Decimal(parent["positions"][instrument]["entry_price"]),
                exit_fill_price=price,
            )
        )
        expected_fee = trade_fee_usdt(
            spec, contracts=size, fill_price=price, fee_rate=Decimal(protocol.fee_rate)
        )
        direction = 1 if fill["order_side"] == "buy" else -1
        if (
            event["fee_rate"] != protocol.fee_rate
            or event["slippage_bps"] != protocol.slippage_bps
            or event["cost_assumption_ref"] != protocol.id
            or event["instrument_ref"] != final["instrument_refs"][instrument]
            or Decimal(fill["filled_notional_usdt"]) != linear_base_quantity(spec, size) * price
            or Decimal(fill["gross_pnl_usdt"]) != expected_gross
            or Decimal(fill["trade_fee_usdt"]) != expected_fee
            or Decimal(child["trade_cash_usdt"]) - Decimal(parent["trade_cash_usdt"])
            != expected_gross - expected_fee
            or Decimal(child["positions"][instrument]["signed_contracts"])
            != held + direction * size
        ):
            raise ValueError("paper_experience_fill_arithmetic_or_cost_invalid")
    cash = Decimal(final["trade_cash_usdt"]) - Decimal(initial["trade_cash_usdt"])
    net, sign = cashflow_label(str(gross), str(fees), str(cash), "0")
    if any(
        Decimal(outcome[key]) != value
        for key, value in (
            ("gross_pnl_usdt", gross),
            ("trade_fees_usdt", fees),
            ("trade_cash_delta_usdt", cash),
            ("funding_cash_usdt", Decimal(0)),
            ("net_pnl_usdt", Decimal(net)),
        )
    ):
        raise ValueError("paper_experience_outcome_cashflow_mismatch")
    times = [
        entry.accepted_at,
        protocol.registered_at,
        _at(initial["created_at"]),
        *(_at(item["executed_at"]) for item in fills),
        published.accepted_at,
        confirmation.accepted_at,
        coverage_receipt.accepted_at,
        as_utc(outcome_row.due_at),
        _at(episode["updated_at"]),
        _at(final["created_at"]),
        contract.known_at,
        coverage.server_before.recorded_at,
        coverage.server_after.recorded_at,
        *(page.capture.recorded_at for page in coverage.pages),
    ]
    known = max(aware_utc(value) for value in times)
    if known > at:
        raise ValueError("paper_experience_source_future")
    case_id = "paper_episode_case_" + canonical_hash(
        [KIND, entry.owner_id, entry.account_key, episode["episode_id"]]
    )
    method_hash = canonical_hash(METHOD)
    version = canonical_hash([outcome_ref, method_hash])
    software = validate_creation_software(
        _creation_software() if creation_software is None else creation_software
    )
    return experience_original(
        dict(
            schema_version=SCHEMA,
            kind=KIND,
            case_id=case_id,
            source_version_key=version,
            owner_id=entry.owner_id,
            account_kind="paper",
            account_id=entry.account_id,
            account_key=entry.account_key,
            instrument_id=partition["instrument_id"],
            profile_ref=final["profile"],
            reference_horizon="4h",
            origin_run_id=snapshot.run_id,
            admission_ref=read_perpetual_admission_identity_in_session(session, snapshot.run_id)[1],
            publication_receipt_ref={"ref": published.request_id, "hash": published.receipt_hash},
            snapshot_ref={"ref": snapshot.id, "hash": snapshot.snapshot_hash},
            revision_ref=_ref(entry.revision),
            final_target_ref=publication["snapshot"]["bindings"]["final_target"],
            frozen_forecast_ref=publication["snapshot"]["bindings"]["frozen_forecast"],
            target_family_ref=publication["target"]["target_family_ref"],
            episode_ref=_ref(episode_ref),
            outcome_ref=outcome_ref,
            entry_intent_ref={"ref": entry.id, "hash": entry.payload_hash},
            confirmation_ref={"ref": confirmation.request_id, "hash": confirmation.receipt_hash},
            protocol_ref=_ref(entry.protocol_ref),
            initial_ledger_ref=initial["revision"],
            final_ledger_ref=final["revision"],
            fills=fills,
            funding_coverage_receipt_ref=episode["funding_coverage_ref"],
            funding_coverage=coverage.body,
            actual_holding_start=fills[0]["executed_at"],
            actual_holding_end=fills[-1]["executed_at"],
            actual_holding_seconds=str(
                Decimal(
                    (_at(fills[-1]["executed_at"]) - _at(fills[0]["executed_at"])).days
                    * 86400000000
                    + (_at(fills[-1]["executed_at"]) - _at(fills[0]["executed_at"])).seconds
                    * 1000000
                    + (_at(fills[-1]["executed_at"]) - _at(fills[0]["executed_at"])).microseconds
                )
                / Decimal(1000000)
            ),
            exit_reason=episode.get("exit_latched_reason"),
            stop_price_usdt=command.stop_price_usdt,
            take_profit_price_usdt=command.take_profit_price_usdt,
            exit_at=command.exit_at,
            contract_ref=final["instrument_refs"][partition["instrument_id"]],
            paper_cost_assumptions=dict(
                fee_rate=protocol.fee_rate,
                fee_status=protocol.fee_status,
                slippage_bps=protocol.slippage_bps,
                slippage_accounting=protocol.slippage_accounting,
            ),
            gross_pnl_usdt=outcome["gross_pnl_usdt"],
            trade_fees_usdt=outcome["trade_fees_usdt"],
            trade_cash_delta_usdt=outcome["trade_cash_delta_usdt"],
            funding_cash_usdt="0",
            net_pnl_usdt=net,
            net_sign=sign,
            known_at=known.isoformat(),
            recorded_at=at.isoformat(),
            calculation_method=METHOD,
            calculation_method_hash=method_hash,
            software=software,
            archive_root=str(funding_source.archive.root.resolve()),
            gaps=list(dict.fromkeys([*outcome["gaps"], "mark_risk_path_unavailable"])),
            probability_scored=False,
            financial_qualified=False,
            skill_consumed=False,
        )
    )


def read_paper_experience_in_session(session, reference, *, cutoff_at):
    ref = DecisionSourceRefV1.model_validate(reference)
    row = session.get(RunRoleOutputRecord, ref.ref, populate_existing=True)
    if row is None:
        raise ValueError("paper_experience_original_missing")
    body = json.loads(row.payload_json)
    at = _at(body["recorded_at"])
    key = canonical_hash([body["case_id"], body["source_version_key"]])
    if (
        row.schema_version != SCHEMA
        or row.role != ROLE + key
        or row.run_id != body["origin_run_id"]
        or row.payload_hash != ref.hash
        or canonical(body) != row.payload_json
        or experience_original(body).ref != ref
        or as_utc(row.created_at) != at
        or at > aware_utc(cutoff_at)
    ):
        raise ValueError("paper_experience_original_invalid_or_future")
    validate_calculation_method(body["calculation_method"], body["calculation_method_hash"])
    software = validate_creation_software(body["software"])
    if body["source_version_key"] != canonical_hash(
        [_ref(body["outcome_ref"]), body["calculation_method_hash"]]
    ):
        raise ValueError("paper_experience_source_identity_invalid")
    source = PerpetualFundingHistorySource(None, RawBodyArchive(Path(body["archive_root"])))
    rebuilt = _derive(
        session,
        episode_ref=body["episode_ref"],
        outcome_ref=body["outcome_ref"],
        funding_source=source,
        recorded_at=at,
        creation_software=software,
    )
    if rebuilt.body_json != row.payload_json:
        raise ValueError("paper_experience_original_changed")
    return rebuilt


class PerpetualPaperExperienceService:
    def __init__(self, database, funding_source, *, clock=utcnow):
        if funding_source.database is not database:
            raise ValueError("paper_experience_database_mismatch")
        self.database, self.funding_source, self.clock = database, funding_source, clock

    read_in_session = staticmethod(read_paper_experience_in_session)

    def prepare(self, *, episode_ref, outcome_ref):
        at = aware_utc(self.clock())
        return prepare_originals(
            self.database,
            lambda session: _derive(
                session,
                episode_ref=episode_ref,
                outcome_ref=outcome_ref,
                funding_source=self.funding_source,
                recorded_at=at,
            ),
        )

    def record(self, *, episode_ref, outcome_ref):
        prepared = self.prepare(episode_ref=episode_ref, outcome_ref=outcome_ref)
        value = prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            prior = session.get(RunRoleOutputRecord, value.ref.ref, populate_existing=True)
            if prior is not None:
                body = json.loads(prior.payload_json)
                old = experience_original(body)
                validate_calculation_method(
                    body["calculation_method"], body["calculation_method_hash"]
                )
                software = validate_creation_software(body["software"])
                # The prepared full source/financial replay must still match;
                # creation time and creation software belong to the first case.
                expected = {
                    **value.body,
                    "recorded_at": body["recorded_at"],
                    "software": software,
                }
                if (
                    prior.schema_version != SCHEMA
                    or prior.run_id != value.body["origin_run_id"]
                    or prior.role
                    != ROLE
                    + canonical_hash([value.body["case_id"], value.body["source_version_key"]])
                    or old.ref.ref != prior.output_id
                    or old.ref.hash != prior.payload_hash
                    or old.body_json != prior.payload_json
                    or expected != body
                    or as_utc(prior.created_at) != _at(body["recorded_at"])
                    or _at(body["recorded_at"]) > aware_utc(self.clock())
                ):
                    raise ValueError("paper_experience_source_version_conflict")
                return old
            key = canonical_hash([value.body["case_id"], value.body["source_version_key"]])
            session.add(
                RunRoleOutputRecord(
                    output_id=value.ref.ref,
                    run_id=value.body["origin_run_id"],
                    role=ROLE + key,
                    schema_version=SCHEMA,
                    payload_json=value.body_json,
                    payload_hash=value.ref.hash,
                    created_at=_at(value.body["recorded_at"]),
                )
            )
            session.flush()
            prepared.verify_after_writes(session)
        return value

    def process_due(self, *, limit=4):
        if type(limit) is not int or not 1 <= limit <= 4:
            raise ValueError("paper_experience_due_limit_invalid")
        with self.database.session() as session:
            body = ActionOutcomeRecord.payload_json
            rows = session.scalars(
                select(ActionOutcomeRecord)
                .where(
                    ActionOutcomeRecord.mode == "paper",
                    ActionOutcomeRecord.status == "settled",
                    ActionOutcomeRecord.due_at <= aware_utc(self.clock()),
                    func.json_extract(body, "$.schema_version") == "perpetual-manual-outcome.v1",
                    ~exists(
                        select(RunRoleOutputRecord.output_id).where(
                            RunRoleOutputRecord.schema_version == SCHEMA,
                            func.json_extract(RunRoleOutputRecord.payload_json, "$.outcome_ref.ref")
                            == ActionOutcomeRecord.outcome_id,
                            func.json_extract(
                                RunRoleOutputRecord.payload_json, "$.outcome_ref.hash"
                            )
                            == ActionOutcomeRecord.payload_hash,
                            func.json_extract(
                                RunRoleOutputRecord.payload_json, "$.calculation_method_hash"
                            )
                            == canonical_hash(METHOD),
                        )
                    ),
                )
                .order_by(ActionOutcomeRecord.due_at, ActionOutcomeRecord.outcome_id)
                .limit(limit)
            ).all()
            pending = []
            for row in rows:
                envelope, outcome = PerpetualExecutionAdapter.artifact(
                    row, "perpetual-manual-outcome.v1"
                )
                episodes = session.scalars(
                    select(ExecutionEpisodeRecord)
                    .where(
                        ExecutionEpisodeRecord.portfolio_id == envelope.account_key,
                        ExecutionEpisodeRecord.episode_id == outcome["episode_id"],
                        func.json_extract(
                            func.json_extract(ExecutionEpisodeRecord.payload_json, "$.body_json"),
                            "$.outcome_id",
                        )
                        == row.outcome_id,
                        func.json_extract(
                            func.json_extract(ExecutionEpisodeRecord.payload_json, "$.body_json"),
                            "$.funding_status",
                        )
                        == "complete_zero_settlements",
                    )
                    .order_by(ExecutionEpisodeRecord.version)
                    .limit(1)
                ).all()
                episode_ref = None
                for episode_row in episodes:
                    e, b = PerpetualExecutionAdapter.artifact(
                        episode_row, "perpetual-manual-episode.v1"
                    )
                    if (
                        b.get("outcome_id") == row.outcome_id
                        and b.get("ledger_revision") == outcome["ledger_revision"]
                        and b.get("funding_status") == "complete_zero_settlements"
                    ):
                        episode_ref = {"ref": e.id, "hash": e.payload_hash}
                        break
                if episode_ref is None:
                    continue
                pending.append((episode_ref, {"ref": row.outcome_id, "hash": row.payload_hash}))
                if len(pending) >= limit:
                    break
        results = []
        for episode_ref, outcome_ref in pending:
            try:
                value = self.record(episode_ref=episode_ref, outcome_ref=outcome_ref)
                results.append(dict(status="recorded", experience_ref=_ref(value.ref)))
            except (ValueError, OSError) as error:
                results.append(dict(status="blocked", outcome_ref=outcome_ref, reason=str(error)))
        return tuple(results)


def paper_experience_context_in_session(
    session,
    *,
    owner_id,
    account_kind,
    account_id,
    instrument_id,
    profile_ref,
    horizon,
    cutoff_at,
    limit=2,
):
    if type(limit) is not int or not 1 <= limit <= 2:
        raise ValueError("paper_experience_context_limit_invalid")
    if account_kind != "paper" or horizon != "4h":
        return ()
    body = RunRoleOutputRecord.payload_json
    profile = _ref(profile_ref)
    ranked = (
        select(
            RunRoleOutputRecord.output_id,
            func.row_number()
            .over(
                partition_by=func.json_extract(body, "$.case_id"),
                order_by=(
                    RunRoleOutputRecord.created_at.desc(),
                    RunRoleOutputRecord.output_id.desc(),
                ),
            )
            .label("case_version"),
        )
        .where(
            RunRoleOutputRecord.schema_version == SCHEMA,
            func.json_extract(body, "$.owner_id") == owner_id,
            func.json_extract(body, "$.account_kind") == account_kind,
            func.json_extract(body, "$.account_id") == account_id,
            func.json_extract(body, "$.instrument_id") == instrument_id,
            func.json_extract(body, "$.reference_horizon") == horizon,
            func.json_extract(body, "$.profile_ref.ref") == profile["ref"],
            func.json_extract(body, "$.profile_ref.hash") == profile["hash"],
            RunRoleOutputRecord.created_at <= aware_utc(cutoff_at),
        )
        .subquery()
    )
    query = (
        select(RunRoleOutputRecord)
        .join(ranked, ranked.c.output_id == RunRoleOutputRecord.output_id)
        .where(ranked.c.case_version == 1)
        .order_by(
            func.json_extract(body, "$.known_at").desc(), RunRoleOutputRecord.output_id.desc()
        )
    )
    newest = session.scalars(query.limit(1)).first()
    rows = [] if newest is None else [newest]
    if newest is not None and limit == 2:
        newest_body = json.loads(newest.payload_json)
        others = query.where(func.json_extract(body, "$.case_id") != newest_body["case_id"])
        opposite = {"gain": "loss", "loss": "gain"}.get(newest_body["net_sign"])
        countercase = (
            session.scalars(
                others.where(func.json_extract(body, "$.net_sign") == opposite).limit(1)
            ).first()
            if opposite
            else None
        )
        if countercase is None:
            countercase = session.scalars(others.limit(1)).first()
        if countercase is not None:
            rows.append(countercase)
    selected = [
        read_paper_experience_in_session(
            session, {"ref": row.output_id, "hash": row.payload_hash}, cutoff_at=cutoff_at
        )
        for row in rows
    ]
    inputs = []
    for value in selected:
        b = value.body
        summary = {
            key: b[key]
            for key in (
                "kind",
                "instrument_id",
                "reference_horizon",
                "actual_holding_start",
                "actual_holding_end",
                "actual_holding_seconds",
                "net_pnl_usdt",
                "net_sign",
                "gross_pnl_usdt",
                "trade_fees_usdt",
                "funding_cash_usdt",
                "gaps",
                "calculation_method_hash",
            )
        }
        inputs.append(
            ResearchInputEvidence(
                evidence_id=value.ref.ref,
                kind="document",
                authority="unverified",
                source_id=SOURCE,
                source_url=None,
                published_at=None,
                observed_at=_at(b["known_at"]),
                received_at=_at(b["recorded_at"]),
                content_hash=value.ref.hash,
                excerpt="历史纸面实际持仓现金流；实际持仓不等于四小时预测，不是胜率/技能指令，不授金融资格。"
                + canonical(summary),
            )
        )
    return tuple(inputs)
