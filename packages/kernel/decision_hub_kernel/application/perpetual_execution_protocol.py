"""Internal account-bound protocol registration, no routes, fills or enablement."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from ..persistence.action_execution_records import ExecutionProtocolRecord
from ..persistence.action_execution_repository import ExecutionRepository, seal
from ..persistence.db import Database, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.perpetual_paper_repository import PerpetualPaperRepository, identity
from ..ports.market_path import aware_utc
from ..ports.perpetual_execution_protocol import (
    PerpetualManualPaperProtocol,
    PerpetualPaperExecutionProtocol,
    protocol_id,
)
from .decision_execution_profile import read_execution_profile
from .okx_perpetual_source import OKXPerpetualSource


class PerpetualPaperProtocolService:
    def __init__(self, database: Database, instrument_source: OKXPerpetualSource, *, clock=utcnow):
        if instrument_source.database is not database:
            raise ValueError("perp_execution_protocol_database_mismatch")
        self.database, self.source, self.clock = database, instrument_source, clock

    def _account_binding(self, session, protocol):
        account = PerpetualPaperRepository(session).revision(protocol.account_revision.ref)
        if (
            account["revision"] != protocol.account_revision.model_dump(mode="json")
            or aware_utc(datetime.fromisoformat(account["created_at"])) > protocol.registered_at
            or account["owner_id"] != protocol.owner_id
            or account["account_id"] != protocol.account_id
            or account["execution_mode"] != protocol.execution_mode
            or account["profile"] != protocol.profile.model_dump(mode="json")
            or account["instrument_refs"]
            != {k: v.model_dump(mode="json") for k, v in protocol.instrument_refs.items()}
        ):
            raise ValueError("perp_execution_protocol_account_binding_mismatch")
        for inst, ref in protocol.instrument_refs.items():
            contract = self.source.read_contract_in_session(session, ref)
            if (
                inst != "okx:" + contract.inst_id
                or contract.ct_val_ccy != contract.base
                or aware_utc(contract.known_at) > protocol.registered_at
            ):
                raise ValueError("perp_execution_protocol_contract_binding_mismatch")
            read_execution_profile(inst, protocol.profile)
        return account

    def read_in_session(
        self, session: Session, reference: DecisionSourceRefV1
    ) -> PerpetualPaperExecutionProtocol:
        row = session.get(ExecutionProtocolRecord, reference.ref, populate_existing=True)
        if row is None:
            raise ValueError("execution_protocol_missing")
        import json

        reader = (
            ExecutionRepository(session).perpetual_manual_protocol
            if json.loads(row.payload_json).get("schema_version") == "perp-manual-paper-protocol.v1"
            else ExecutionRepository(session).perpetual_protocol
        )
        value = reader(reference.ref)
        if value.reference != reference:
            raise ValueError("perp_execution_protocol_reference_mismatch")
        self._account_binding(session, value)
        return value

    def register(
        self, *, owner_id: str, account_id: str, trial_id: str, start_at: datetime, end_at: datetime
    ) -> PerpetualPaperExecutionProtocol:
        return self._register(
            owner_id=owner_id,
            account_id=account_id,
            trial_id=trial_id,
            start_at=start_at,
            end_at=end_at,
            manual=False,
        )

    def register_manual(
        self, *, owner_id: str, account_id: str, trial_id: str, start_at: datetime, end_at: datetime
    ) -> PerpetualManualPaperProtocol:
        return self._register(
            owner_id=owner_id,
            account_id=account_id,
            trial_id="manual:" + identity(trial_id),
            start_at=start_at,
            end_at=end_at,
            manual=True,
        )

    def _register(self, *, owner_id, account_id, trial_id, start_at, end_at, manual):
        """Explicit internal configuration only. Does not opt in the account or action."""
        owner_id, account_id, trial_id = (
            identity(owner_id),
            identity(account_id),
            identity(trial_id),
        )
        start, end = aware_utc(start_at), aware_utc(end_at)
        read_at = aware_utc(self.clock())
        key = protocol_id(owner_id, account_id, trial_id, start, end)

        def read(session):
            old = session.get(ExecutionProtocolRecord, key, populate_existing=True)
            if old is not None:
                return self.read_in_session(
                    session, DecisionSourceRefV1(ref=key, hash=old.payload_hash)
                )
            account = PerpetualPaperRepository(session).current(owner_id, account_id)
            if aware_utc(datetime.fromisoformat(account["created_at"])) > read_at:
                raise ValueError("perp_execution_protocol_account_not_known")
            for inst, ref in account["instrument_refs"].items():
                contract = self.source.read_contract_in_session(
                    session, DecisionSourceRefV1.model_validate(ref)
                )
                if (
                    "okx:" + contract.inst_id != inst
                    or contract.ct_val_ccy != contract.base
                    or aware_utc(contract.known_at) > read_at
                ):
                    raise ValueError("perp_execution_protocol_contract_binding_mismatch")
                read_execution_profile(inst, DecisionSourceRefV1.model_validate(account["profile"]))
            return account

        prepared = prepare_originals(self.database, read)
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            if isinstance(prepared.value, PerpetualPaperExecutionProtocol):
                return prepared.value
            account = prepared.value
            if manual and account["execution_mode"] != "manual_after_visible":
                raise ValueError("perp_manual_account_mode_required")
            at = aware_utc(self.clock())
            if at < read_at:
                raise ValueError("perp_execution_protocol_clock_backwards")
            value = seal(
                PerpetualManualPaperProtocol if manual else PerpetualPaperExecutionProtocol,
                dict(
                    schema_version="perp-manual-paper-protocol.v1"
                    if manual
                    else "perp-quote-paper-protocol.v1",
                    model="perp_quote_paper.v1",
                    id=key,
                    trial_id=trial_id,
                    owner_id=owner_id,
                    account_id=account_id,
                    account_revision=account["revision"],
                    instrument_refs=account["instrument_refs"],
                    profile=account["profile"],
                    execution_mode=account["execution_mode"],
                    registered_at=at,
                    T0=start,
                    T1=end,
                    reaction_delay_seconds=1,
                    quote_max_age_seconds=5,
                    entry_wait_seconds=30,
                    top_size_participation="0.1",
                    position_mode="net",
                    margin_mode="isolated",
                    initial_notional_to_margin_ratio="1",
                    quote_selection="first_observed_interval_lower_strictly_after_eligible",
                    fill_basis="buy_ask_sell_bid_then_adverse_slippage",
                    order_kind="market_after_eligible",
                    size_unit="contracts",
                    same_account_quote_capacity="cumulative_no_reuse",
                    entry_partial_remainder="cancel",
                    exit_partial_remainder="retain_position_and_protection_retry_new_quote_alarm_after_30s",
                    stress_reaction_delays_seconds=(5, 15),
                    fee_rate="0.0005",
                    fee_basis="abs_base_quantity_times_actual_simulated_fill_price",
                    fee_status="explicit_paper_assumption_not_authenticated_account_fee_tier",
                    slippage_bps="2",
                    slippage_accounting="included_in_fill_price_no_second_cash_deduction",
                    size_policy_ref=None,
                    exit_policy_ref=None,
                    required_reviewed_originals=()
                    if manual
                    else ("size_policy", "exit_and_protection_policy"),
                    execution_authority=False,
                    **(
                        dict(
                            required_user_originals=("manual_size", "manual_exit"),
                            policy_authority="user_explicit_paper_simulation",
                            native_reviewed=False,
                        )
                        if manual
                        else {}
                    ),
                ),
            )
            writer = (
                ExecutionRepository(session).register_perpetual_manual_protocol
                if manual
                else ExecutionRepository(session).register_perpetual_protocol
            )
            result = writer(value, now=at)
            prepared.verify_after_writes(session)
            return result
