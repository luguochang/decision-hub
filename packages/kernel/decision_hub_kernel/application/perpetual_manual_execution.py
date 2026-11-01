"""Owner-authorized independent paper simulation; never native financial authority."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.action_execution_repository import canonical, seal
from ..persistence.action_quote_sampling_records import QuoteSamplingHead
from ..persistence.action_repository import ActionRepository, make_command_receipt
from ..persistence.callback_source_lease import callback_source_lease_scope
from ..persistence.db import RawCaptureRecord, RunRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.perpetual_execution_adapter import PerpetualExecutionAdapter
from ..persistence.perpetual_paper_repository import PerpetualPaperRepository, account_key, identity
from ..ports.exact_decimal_text import number
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_execution_protocol import PerpetualManualPaperProtocol
from ..ports.perpetual_manual_execution import (
    MANUAL_INSTRUMENT_IDS,
    EventManualBinding,
    EventManualIdentity,
    ManualExecutionCommand,
    ManualExecutionIntent,
    ManualIdentity,
    manual_horizon,
    manual_scope,
    parse_manual_display,
    parse_manual_execution,
    parse_manual_preparation,
)
from .decision_settings import DecisionSettingsService
from .event_scenario_manual import read_event_manual_in_session
from .perpetual_advice_query import PUBLISHER, PerpetualAdviceQuery
from .perpetual_condition_monitor import condition_new_exposure_blocked_in_session
from .perpetual_execution_protocol import PerpetualPaperProtocolService
from .perpetual_paper import PerpetualPaperService
from .perpetual_research_binding import read_perpetual_admission_identity_in_session


class PerpetualManualPaperService:
    def __init__(
        self,
        database,
        paper_service,
        protocol_service,
        *,
        clock=utcnow,
        funding_history=None,
    ):
        if (
            not isinstance(paper_service, PerpetualPaperService)
            or not isinstance(protocol_service, PerpetualPaperProtocolService)
            or paper_service.database is not database
            or protocol_service.database is not database
        ):
            raise ValueError("perp_manual_actual_services_required")
        self.database, self.paper, self.protocols, self.clock = (
            database,
            paper_service,
            protocol_service,
            clock,
        )
        self.funding_history = funding_history

    @staticmethod
    def _digest(owner, command):
        identity(owner)
        return canonical_hash({"owner_id": owner, "command": command.model_dump(mode="json")})

    def read_command(self, owner_id, request_id, expected_command_hash=None):
        with self.database.session() as session:
            value = ActionRepository(session).get_command(identity(owner_id), identity(request_id))
            if value is not None:
                if value.operation not in {"perp_prepare", "perp_display", "perp_manual_intent"}:
                    raise ValueError("perp_manual_command_version_invalid")
                if (
                    expected_command_hash is not None
                    and value.payload_hash != expected_command_hash
                ):
                    raise ValueError("perp_manual_request_conflict")
            return value

    def prepare_context(self, owner_id, command):
        """Signed explicit configuration, separate from display and execute authority."""
        command = parse_manual_preparation(command)
        event = isinstance(command, EventManualBinding)
        owner_id = identity(owner_id)
        old = self._existing(owner_id, command)
        if old is not None:
            return old
        now = aware_utc(self.clock())
        if (
            command.partition.account_kind != "paper"
            or command.partition.instrument_id not in MANUAL_INSTRUMENT_IDS
        ):
            raise ValueError("perp_manual_paper_swap_required")
        view = (
            None
            if event
            else PerpetualAdviceQuery(self.database, now=lambda: now).current(
                owner_id,
                **command.partition.model_dump(mode="json"),
                expected_settings_ref=command.settings_revision.ref,
                expected_settings_hash=command.settings_revision.hash,
                expected_settings_state_version=command.settings_state_version,
                horizon=command.horizon,
            )
        )
        if not event and (
            view["status"] != "available"
            or view["revision"]["revision_id"] != command.revision.ref
            or view["revision"]["payload_hash"] != command.revision.hash
        ):
            raise ValueError("perp_manual_current_advice_unavailable")

        def read(session: Session) -> dict[str, Any]:
            current = DecisionSettingsService.current_in_session(
                session, owner_id, command.partition
            )
            if (
                current is None
                or current.state_version != command.settings_state_version
                or (current.revision.revision_id, current.revision.payload_hash)
                != (command.settings_revision.ref, command.settings_revision.hash)
            ):
                raise ValueError("perp_manual_settings_changed")
            account, position = self.paper.account_reader(session, owner_id, command.partition)
            ledger = PerpetualPaperRepository(session).current(
                owner_id, command.partition.account_id
            )
            if (
                position.state != "flat"
                or account.available_margin is None
                or any(Decimal(p["signed_contracts"]) for p in ledger["positions"].values())
                or (
                    not event
                    and account.original.model_dump(mode="json")
                    != view["context"]["account_snapshot"]["original"]
                )
            ):
                raise ValueError("perp_manual_known_flat_account_required")
            if event:
                facts = read_event_manual_in_session(
                    session,
                    owner_id=owner_id,
                    command=command,
                    at=now,
                )
                PerpetualExecutionAdapter(session).require_idle(ledger["account_key"])
                return dict(
                    ledger=ledger, plan_state_version=facts["head_state_version"], execution=facts
                )
            candidate = view["revision"]["selected_candidate"]
            if candidate["action"] != "open" or candidate["target_side"] not in {"long", "short"}:
                raise ValueError("perp_manual_selected_candidate_not_entry")
            head = ActionRepository(session).get_head(view["revision"]["plan_id"])
            if head.current_revision_id != command.revision.ref:
                raise ValueError("perp_manual_revision_changed_or_terminal")
            PerpetualExecutionAdapter(session).require_idle(ledger["account_key"])
            return dict(
                ledger=ledger,
                plan_state_version=head.state_version,
                execution=dict(plan_id=view["revision"]["plan_id"], target=view["target"]),
            )

        prepared = prepare_originals(self.database, read)
        if prepared.value["execution"]["target"]["horizon"] != command.horizon:
            raise ValueError("perp_manual_horizon_target_mismatch")
        # Configuration activates prospectively; it cannot consume a quote.
        protocol = self.protocols.register_manual(
            owner_id=owner_id,
            account_id=command.partition.account_id,
            trial_id="session:" + command.request_id,
            start_at=aware_utc(self.clock()) + timedelta(seconds=2),
            end_at=aware_utc(
                datetime.fromisoformat(prepared.value["execution"]["target"]["window_end"])
            ),
        )
        protocol_prepared = prepare_originals(
            self.database,
            lambda session: self.protocols.read_in_session(session, protocol.reference),
        )
        context: dict[str, Any] = dict(
            partition=command.partition.model_dump(mode="json"),
            settings_revision=command.settings_revision.model_dump(mode="json"),
            settings_state_version=command.settings_state_version,
            revision=command.revision.model_dump(mode="json"),
            expected_plan_state_version=prepared.value["plan_state_version"],
            account_revision=prepared.value["ledger"]["revision"],
            account_state_version=prepared.value["ledger"]["state_version"],
            protocol_ref=protocol.reference.model_dump(mode="json"),
        )
        if command.horizon != "4h":
            context["horizon"] = command.horizon
        if event:
            context.update(prepared.value["execution"]["event_binding"])
        with self.database.session() as session:
            begin_immediate(session)
            repo = ActionRepository(session)
            old = repo.get_command(owner_id, command.request_id)
            if old:
                if old.payload_hash != self._digest(owner_id, command):
                    raise ValueError("perp_manual_request_conflict")
                return old
            prepared.verify(session)
            protocol_prepared.verify(session)
            receipt = make_command_receipt(
                owner_id=owner_id,
                request_id=command.request_id,
                plan_id=prepared.value["execution"]["plan_id"],
                operation="perp_prepare",
                payload_hash=self._digest(owner_id, command),
                accepted_at=aware_utc(self.clock()),
                response=dict(
                    schema_version="perpetual-manual-command-result.v1",
                    status="prepared",
                    command=command.model_dump(mode="json"),
                    context=context,
                    protocol=protocol.model_dump(mode="json"),
                    native_reviewed=False,
                    financial_qualified=False,
                    policy_authority="user_explicit_paper_simulation",
                ),
            )
            repo.append_command_receipt(receipt)
            prepared.verify_after_writes(session)
            protocol_prepared.verify_after_writes(session)
            return receipt

    def _existing(self, owner, command):
        return self.read_command(owner, command.request_id, self._digest(owner, command))

    def _prepare(
        self, owner, command, *, accepted_intent=None,
        session: Session | None = None, at: datetime | None = None,
    ):
        at = aware_utc(self.clock() if at is None else at)
        partition = command.partition
        event = isinstance(command, EventManualBinding)
        if (
            partition.account_kind != "paper"
            or partition.instrument_id not in MANUAL_INSTRUMENT_IDS
        ):
            raise ValueError("perp_manual_paper_swap_required")
        query = PerpetualAdviceQuery(self.database, now=lambda: at)
        parameters = dict(
            **partition.model_dump(mode="json"),
            expected_settings_ref=command.settings_revision.ref,
            expected_settings_hash=command.settings_revision.hash,
            expected_settings_state_version=command.settings_state_version,
            horizon=manual_horizon(command),
        )
        view = None if event else (
            query.current(**dict(owner_id=owner, **parameters)) if session is None
            else query.current_in_session(session, owner, at=at, **parameters)
        )
        if not event and view["status"] != "available":
            raise ValueError("perp_manual_current_advice_unavailable")

        def read(session):
            current = DecisionSettingsService.current_in_session(session, owner, partition)
            if (
                current is None
                or current.state_version != command.settings_state_version
                or (current.revision.revision_id, current.revision.payload_hash)
                != (command.settings_revision.ref, command.settings_revision.hash)
            ):
                raise ValueError("perp_manual_settings_changed")
            repo = ActionRepository(session)
            verified = None
            execution: dict[str, Any]
            if event:
                execution = read_event_manual_in_session(
                    session,
                    owner_id=owner,
                    command=command,
                    at=at,
                )
                if execution["head_state_version"] != command.expected_plan_state_version:
                    raise ValueError("perp_manual_revision_changed_or_terminal")
            else:
                run = session.get(RunRecord, view["run_id"], populate_existing=True)
                admission, admission_ref = read_perpetual_admission_identity_in_session(
                    session, run.run_id
                )
                if admission["owner_id"] != owner or admission["partition"] != partition.model_dump(
                    mode="json"
                ):
                    raise PermissionError("perp_manual_owner_binding_invalid")
                published = repo.get_command(
                    PUBLISHER,
                    "publish_" + canonical_hash([run.run_id, admission["opportunity_id"]]),
                )
                verified = PerpetualAdviceQuery._published(
                    session, repo, run, admission, admission_ref, published, deepcopy(view), at
                )
                revision = verified["revision"]
                head = repo.get_head(revision["plan_id"])
                if (
                    (revision["revision_id"], revision["payload_hash"])
                    != (command.revision.ref, command.revision.hash)
                    or head.current_revision_id != command.revision.ref
                    or head.state_version != command.expected_plan_state_version
                    or head.state in {"completed", "expired", "invalidated", "superseded"}
                    or verified["status"] != "available"
                ):
                    raise ValueError("perp_manual_revision_changed_or_terminal")
                candidate = revision["selected_candidate"]
                if candidate["action"] != "open" or candidate["target_side"] not in {
                    "long",
                    "short",
                }:
                    raise ValueError("perp_manual_selected_candidate_not_entry")
                execution = dict(
                    plan_id=revision["plan_id"],
                    candidate_id=candidate["candidate_id"],
                    target_side=candidate["target_side"],
                    target=verified["target"],
                    published_at=revision["published_at"],
                    latest_entry_at=head.valid_until.isoformat(),
                )
            if execution["target"]["horizon"] != manual_horizon(command):
                raise ValueError("perp_manual_horizon_target_mismatch")
            ledger = PerpetualPaperRepository(session).current(owner, partition.account_id)
            account, position = self.paper.account_reader(session, owner, partition)
            if (
                ledger["revision"] != command.account_revision.model_dump(mode="json")
                or ledger["state_version"] != command.account_state_version
                or (
                    not event
                    and verified["context"]["account_snapshot"]["original"] != ledger["revision"]
                )
                or account.equity is None
                or account.available_margin is None
                or position.state != "flat"
                or any(Decimal(p["signed_contracts"]) != 0 for p in ledger["positions"].values())
            ):
                raise ValueError("perp_manual_known_flat_account_required")
            protocol = self.protocols.read_in_session(session, command.protocol_ref)
            if (
                not isinstance(protocol, PerpetualManualPaperProtocol)
                or protocol.owner_id != owner
                or protocol.account_id != partition.account_id
                or not protocol.T0 <= at < protocol.T1
            ):
                raise ValueError("perp_manual_protocol_scope_invalid")
            latest_entry_at = min(
                aware_utc(datetime.fromisoformat(execution["latest_entry_at"])),
                aware_utc(datetime.fromisoformat(execution["target"]["window_end"])),
                protocol.T1,
            )
            contract = self.paper.source.read_contract_in_session(
                session, command.partition and current.revision.values.instrument
            )
            scope = manual_scope(partition.instrument_id.removeprefix("okx:"))
            sampling = session.get(QuoteSamplingHead, scope, populate_existing=True)
            planned_loss_limit = None
            if isinstance(command, ManualExecutionCommand):
                shown = repo.get_command(owner, command.display_receipt.ref)
                identity_fields = (
                    EventManualIdentity.model_fields if event else ManualIdentity.model_fields
                )
                if (
                    shown is None
                    or shown.operation != "perp_display"
                    or shown.receipt_hash != command.display_receipt.hash
                    or shown.accepted_at > at
                    or {k: shown.response["command"].get(k) for k in identity_fields}
                    != {k: command.model_dump(mode="json").get(k) for k in identity_fields}
                ):
                    raise ValueError("perp_manual_display_original_required")
                exit_at = aware_utc(datetime.fromisoformat(command.exit_at))
                end = min(
                    aware_utc(datetime.fromisoformat(execution["target"]["window_end"])),
                    protocol.T1,
                )
                if not at + timedelta(seconds=1) < exit_at <= end:
                    raise ValueError("perp_manual_exit_window_invalid")
                for price in (command.stop_price_usdt, command.take_profit_price_usdt):
                    if Decimal(price) % Decimal(contract.tick_sz):
                        raise ValueError("perp_manual_exit_price_tick_invalid")
                amount = Decimal(command.max_entry_notional_usdt)
                if amount * (1 + Decimal(protocol.fee_rate)) > Decimal(account.available_margin):
                    raise ValueError("perp_manual_insufficient_budget")
                from ..decision.action_comparison import _policy_limit

                utility = current.revision.values.utility_policy
                cap = _policy_limit(
                    utility.max_gross_notional.model_dump(mode="json")
                    if utility.max_gross_notional
                    else None,
                    Decimal(account.equity),
                )
                if cap is not None and amount > cap:
                    raise ValueError("perp_manual_user_risk_limit_exceeded")
                if (
                    utility.allowed_sides is not None
                    and execution["target_side"] not in utility.allowed_sides
                    or utility.allowed_leverage is not None
                    and Decimal(1) not in map(Decimal, utility.allowed_leverage)
                ):
                    raise ValueError("perp_manual_user_side_or_leverage_forbidden")
                planned_loss_limit = _policy_limit(
                    utility.max_planned_loss.model_dump(mode="json")
                    if utility.max_planned_loss
                    else None,
                    Decimal(account.equity),
                )
                if utility.tail_constraints or utility.margin_buffer_policy is not None:
                    raise ValueError("perp_manual_additional_risk_policy_not_supported")
                if accepted_intent is None:
                    PerpetualExecutionAdapter(session).require_idle(ledger["account_key"])
            return dict(
                view=verified,
                execution=execution,
                ledger=ledger,
                protocol=protocol.model_dump(mode="json"),
                latest_entry_at=latest_entry_at.isoformat(),
                planned_loss_limit_usdt=(
                    number(planned_loss_limit) if planned_loss_limit is not None else None
                ),
                sampling_generation=(sampling.generation - int(sampling.owner_id is not None))
                if sampling
                else 0,
            )

        return prepare_originals(self.database, read) if session is None else read(session)

    def _record(self, owner, command, operation):
        old = self._existing(owner, command)
        if old is not None:
            return old
        prepared = self._prepare(owner, command)
        with self.database.session() as session:
            begin_immediate(session)
            repo = ActionRepository(session)
            old = repo.get_command(owner, command.request_id)
            digest = self._digest(owner, command)
            if old is not None:
                if old.payload_hash != digest or old.operation != operation:
                    raise ValueError("perp_manual_request_conflict")
                return old
            prepared.verify(session)
            now = aware_utc(self.clock())
            value = prepared.value
            execution = value["execution"]
            if isinstance(command, ManualExecutionCommand) and (
                condition_new_exposure_blocked_in_session(
                    session,
                    owner_id=owner,
                    plan_id=execution["plan_id"],
                    revision_ref=command.revision.model_dump(mode="json"),
                )
            ):
                raise ValueError("perpetual_condition_invalidated_new_exposure")
            latest_entry_at = aware_utc(datetime.fromisoformat(value["latest_entry_at"]))
            if now >= latest_entry_at:
                raise ValueError("perp_manual_entry_permission_elapsed")
            response = dict(
                schema_version="perpetual-manual-command-result.v1",
                status="recorded",
                command=command.model_dump(mode="json"),
                native_reviewed=False,
                financial_qualified=False,
                policy_authority="user_explicit_paper_simulation",
                server_time=now.isoformat(),
            )
            if isinstance(command, ManualExecutionCommand):
                PerpetualExecutionAdapter(session).require_idle(value["ledger"]["account_key"])
                eligible = max(
                    now, aware_utc(datetime.fromisoformat(execution["published_at"]))
                ) + timedelta(seconds=1)
                deadline = min(
                    eligible + timedelta(seconds=30),
                    latest_entry_at,
                    aware_utc(datetime.fromisoformat(command.exit_at)),
                )
                if deadline <= eligible:
                    raise ValueError("perp_manual_entry_window_elapsed")
                intent = seal(
                    ManualExecutionIntent,
                    dict(
                        **({"horizon": command.horizon} if command.horizon else {}),
                        schema_version="perpetual-manual-execution-intent.v1",
                        id="perp_intent_" + canonical_hash([owner, command.request_id]),
                        owner_id=owner,
                        account_id=command.partition.account_id,
                        account_key=account_key(owner, command.partition.account_id),
                        protocol_ref=command.protocol_ref,
                        scope_id=manual_scope(command.partition.instrument_id.removeprefix("okx:")),
                        event_key="manual_" + digest,
                        plan_id=execution["plan_id"],
                        revision=command.revision,
                        candidate_id=execution["candidate_id"],
                        target_side=execution["target_side"],
                        phase="entry",
                        command_request_id=command.request_id,
                        command_hash=digest,
                        command_json=canonical(command.model_dump(mode="json")),
                        accepted_at=now,
                        eligible_at=eligible,
                        quote_deadline=deadline,
                        sampling_generation=value["sampling_generation"],
                        episode_ref=None,
                        trigger_ref=None,
                        native_reviewed=False,
                        financial_qualified=False,
                        policy_authority="user_explicit_paper_simulation",
                    ),
                )
                PerpetualExecutionAdapter(session).append_intent(intent)
                response.update(status="queued", intent=intent.model_dump(mode="json"))
            receipt = make_command_receipt(
                owner_id=owner,
                request_id=command.request_id,
                plan_id=execution["plan_id"],
                operation=operation,
                payload_hash=digest,
                response=response,
                accepted_at=now,
            )
            repo.append_command_receipt(receipt)
            prepared.verify_after_writes(session)
            return receipt

    def record_display(self, owner_id, command):
        return self._record(identity(owner_id), parse_manual_display(command), "perp_display")

    def submit_intent(self, owner_id, command):
        return self._record(
            identity(owner_id), parse_manual_execution(command), "perp_manual_intent"
        )

    @staticmethod
    def _authority(session, intent):
        adapter = PerpetualExecutionAdapter(session)
        command = parse_manual_execution(intent.command_json)
        if (
            intent.scope_id != manual_scope(command.partition.instrument_id.removeprefix("okx:"))
            or intent.account_id != command.partition.account_id
            or intent.account_key != account_key(intent.owner_id, command.partition.account_id)
            or manual_horizon(intent) != manual_horizon(command)
        ):
            raise ValueError("perp_manual_intent_partition_scope_mismatch")
        receipt = ActionRepository(session).get_command(intent.owner_id, intent.command_request_id)
        if (
            receipt is None
            or receipt.operation != "perp_manual_intent"
            or receipt.payload_hash != intent.command_hash
            or receipt.response["command"] != command.model_dump(mode="json")
            or canonical(receipt.response["command"]) != intent.command_json
            or intent.command_hash != PerpetualManualPaperService._digest(intent.owner_id, command)
        ):
            raise ValueError("perp_manual_original_authority_missing")
        entry = adapter.intent(receipt.response["intent"]["id"])
        if receipt.response["intent"] != entry.model_dump(mode="json"):
            raise ValueError("perp_manual_original_intent_substitution")
        if intent.phase == "entry":
            if intent != entry:
                raise ValueError("perp_manual_original_entry_substitution")
        else:
            episode = adapter.episode(intent.account_key)
            if (
                episode is None
                or intent.episode_ref is None
                or episode[1] != intent.episode_ref.model_dump(mode="json")
                or episode[0]["entry_intent_id"] != entry.id
            ):
                raise ValueError("perp_manual_exit_episode_substitution")
            trigger = ActionRepository(session).get_command(
                "system:perp-paper:v1", intent.trigger_ref.ref
            )
            if (
                trigger is None
                or trigger.operation != "perp_exit_trigger"
                or trigger.receipt_hash != intent.trigger_ref.hash
                or trigger.response["episode_ref"] != episode[1]
                or trigger.response["entry_intent_id"] != entry.id
            ):
                raise ValueError("perp_manual_exit_trigger_missing")
        return command

    def _account_in_session(
        self, session: Session, intent: ManualExecutionIntent
    ) -> dict[str, Any]:
        self._authority(session, intent)
        protocol = self.protocols.read_in_session(session, intent.protocol_ref)
        if not isinstance(protocol, PerpetualManualPaperProtocol):
            raise ValueError("perp_manual_protocol_version_required")
        ledger = PerpetualPaperRepository(session).current(intent.owner_id, intent.account_id)
        episode = PerpetualExecutionAdapter(session).episode(intent.account_key)
        return dict(ledger=ledger, episode=episode)

    def _observations(self, intent, *, session: Session | None = None):
        from .perpetual_quote_sampling import PerpetualQuoteSamplingAdapter

        def read(session):
            stored = PerpetualExecutionAdapter(session).intent(intent.id)
            if stored != intent:
                raise ValueError("perp_manual_intent_changed")
            self._authority(session, intent)
            rows = PerpetualQuoteSamplingAdapter.observations(
                session, generation=intent.sampling_generation, scope_id=intent.scope_id
            )
            for entry in rows:
                body = entry["body"]
                if body["event"] == "captured":
                    if len(body["capture_ids"]) != 2 or body["quote_id"] != body["capture_ids"][0]:
                        raise ValueError("perp_manual_bracket_original_required")
                    captures = [
                        session.get(RawCaptureRecord, x, populate_existing=True)
                        for x in body["capture_ids"]
                    ]
                    if any(x is None for x in captures):
                        raise ValueError("perp_manual_sampling_capture_missing")
                    entry["cutoff"] = max(as_utc(x.recorded_at) for x in captures)
            return rows

        return prepare_originals(self.database, read) if session is None else read(session)

    def _quote(self, entry, instrument_ref):
        from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
            DecisionSourceRefV1,
        )

        body = entry["body"]
        return self.paper.prepare_quote(
            instrument_ref=DecisionSourceRefV1.model_validate(instrument_ref),
            capture_id=body["capture_ids"][0],
            clock_measurement_id=body["capture_ids"][1],
            cutoff=entry["cutoff"],
        )

    def _terminal_entry(self, intent, command):
        """Withdraw only a stale entry; never terminate a protective exit."""
        if intent.phase != "entry":
            return None
        at = aware_utc(self.clock())

        def read(session):
            stored = PerpetualExecutionAdapter(session).intent(intent.id)
            if stored != intent:
                raise ValueError("perp_manual_intent_changed")
            self._authority(session, intent)
            if condition_new_exposure_blocked_in_session(
                session,
                owner_id=intent.owner_id,
                plan_id=intent.plan_id,
                revision_ref=intent.revision.model_dump(mode="json"),
            ):
                return "perpetual_condition_invalidated_new_exposure"
            if at > intent.quote_deadline:
                return "entry_deadline_elapsed"
            current = DecisionSettingsService.current_in_session(
                session, intent.owner_id, command.partition
            )
            if (
                current is None
                or current.state_version != command.settings_state_version
                or (current.revision.revision_id, current.revision.payload_hash)
                != (command.settings_revision.ref, command.settings_revision.hash)
            ):
                return "perp_manual_settings_changed"
            if isinstance(command, EventManualBinding):
                try:
                    facts = read_event_manual_in_session(
                        session,
                        owner_id=intent.owner_id,
                        command=command,
                        at=at,
                    )
                except (ValueError, PermissionError) as error:
                    return (
                        str(error)
                        if str(error).startswith("perp_manual_")
                        else ("perp_manual_event_original_unavailable")
                    )
                ledger = PerpetualPaperRepository(session).current(
                    intent.owner_id,
                    command.partition.account_id,
                )
                if (
                    ledger["revision"] != command.account_revision.model_dump(mode="json")
                    or ledger["state_version"] != command.account_state_version
                ):
                    return "perp_manual_account_changed"
                if facts["head_state_version"] != command.expected_plan_state_version:
                    return "entry_plan_changed_or_terminal"
                if (
                    facts["target_side"] != intent.target_side
                    or facts["candidate_id"] != intent.candidate_id
                ):
                    return "perp_manual_event_selection_changed"
                protocol = self.protocols.read_in_session(session, intent.protocol_ref)
                if not isinstance(protocol, PerpetualManualPaperProtocol):
                    raise ValueError("perp_manual_protocol_version_required")
                if at >= min(
                    aware_utc(datetime.fromisoformat(facts["latest_entry_at"])), protocol.T1
                ):
                    return "entry_permission_elapsed"
                return None
            head = ActionRepository(session).get_head(intent.plan_id)
            if (
                head is None
                or head.current_revision_id != intent.revision.ref
                or head.state_version != command.expected_plan_state_version
                or head.state in {"completed", "expired", "invalidated", "superseded"}
            ):
                return "entry_plan_changed_or_terminal"
            protocol = self.protocols.read_in_session(session, intent.protocol_ref)
            if not isinstance(protocol, PerpetualManualPaperProtocol):
                raise ValueError("perp_manual_protocol_version_required")
            if at >= min(head.valid_until, protocol.T1):
                return "entry_permission_elapsed"
            return None

        # An unchanged entry still goes through the existing full fill readset.
        # Only a prospective no-fill needs this additional writer readset.
        with self.database.session() as session:
            if read(session) is None:
                return None
        prepared = prepare_originals(self.database, read)
        if prepared.value is None:
            return None
        with self.database.session() as session:
            begin_immediate(session)
            adapter = PerpetualExecutionAdapter(session)
            old = adapter.result(intent)
            if old is not None:
                return old
            prepared.verify(session)
            now = aware_utc(self.clock())
            result = dict(
                schema_version="perpetual-manual-execution-result.v1",
                intent_id=intent.id,
                status="no_fill",
                reason=prepared.value,
                decided_at=now.isoformat(),
                quote_selection_proof=[],
                fill_id=None,
                ledger_revision=None,
                episode_id=None,
            )
            adapter.append_result(intent, result, now)
            prepared.verify_after_writes(session)
            return result

    @staticmethod
    def _planned_stop_loss(
        spec, *, contracts, entry_price, stop_price, target_side, protocol, limit
    ):
        """An explicit stop-trigger trade scenario, not a bound on future net loss.

        Stop observes executable bid for long / ask for short, so that side is
        already the trigger price. Apply adverse slippage/tick once at exit;
        future gaps, funding and liquidation remain outside this trade estimate.
        """
        from ..decision.perpetual_accounting import (
            execution_price_usdt,
            linear_gross_pnl_usdt,
            trade_fee_usdt,
        )

        exit_price = execution_price_usdt(
            spec,
            order_side="sell" if target_side == "long" else "buy",
            bid=stop_price,
            ask=stop_price,
            slippage_bps=Decimal(protocol["slippage_bps"]),
        )
        gross = linear_gross_pnl_usdt(
            spec,
            side=target_side,
            contracts=contracts,
            entry_fill_price=entry_price,
            exit_fill_price=exit_price,
        )
        entry_fee = trade_fee_usdt(
            spec,
            contracts=contracts,
            fill_price=entry_price,
            fee_rate=Decimal(protocol["fee_rate"]),
        )
        exit_fee = trade_fee_usdt(
            spec,
            contracts=contracts,
            fill_price=exit_price,
            fee_rate=Decimal(protocol["fee_rate"]),
        )
        with localcontext() as context:
            context.prec = 100
            loss = max(Decimal(0), -gross + entry_fee + exit_fee)
        return dict(
            schema_version="perpetual-manual-planned-stop-trade-loss.v1",
            scope="stop_trigger_trade_scenario_only",
            contracts=number(contracts),
            entry_fill_price_usdt=number(entry_price),
            stop_trigger_price_usdt=number(stop_price),
            stop_trigger_basis="bid" if target_side == "long" else "ask",
            estimated_stop_fill_price_usdt=number(exit_price),
            gross_pnl_usdt=number(gross),
            entry_fee_usdt=number(entry_fee),
            exit_fee_usdt=number(exit_fee),
            planned_trade_loss_usdt=number(loss),
            limit_usdt=number(limit),
            within_limit=loss <= limit,
            funding_cash_usdt=None,
            net_loss_bound_usdt=None,
            gap_risk_included=False,
            liquidation_risk_included=False,
            guaranteed_loss_cap=False,
        )

    @staticmethod
    def _episode_predecessor(previous, intent):
        """A new position owns fresh policy/state, while the account CAS never resets."""
        head_version = previous[0]["version"] if previous else 0
        if intent.phase == "entry":
            return "perp_episode_" + canonical_hash(intent.id), head_version, {}
        return previous[0]["episode_id"], head_version, previous[0]

    def execute_intent(self, intent_id, *, checkpoint: Callable[[], None] | None = None):
        from ..decision.perpetual_accounting import (
            execution_price_usdt,
            floor_contracts,
            linear_base_quantity,
        )

        with self.database.session() as session:
            adapter = PerpetualExecutionAdapter(session)
            intent = adapter.intent(identity(intent_id))
            self._authority(session, intent)
            old = adapter.result(intent)
            if old is not None:
                return old
        now = aware_utc(self.clock())
        if now < intent.eligible_at:
            return None
        command = parse_manual_execution(intent.command_json)
        instrument_id = command.partition.instrument_id
        terminal = self._terminal_entry(intent, command)
        if terminal is not None:
            return terminal
        from .perpetual_manual_preparation import prepare_manual_execution
        from .perpetual_paper import require_prepared_bundle, verify_prepared_bundle_after_writes

        with callback_source_lease_scope(checkpoint) as source_lease:
            preparation = prepare_manual_execution(
                self, intent, command, now, checkpoint=checkpoint, source_lease=source_lease
            )
            value = preparation.value.value
            entry_auth, account_auth = value["entry_auth"], value["account_auth"]
            observations, proof = value["observations"], value["proof"]
            selected = (
                (value["selected_entry"], preparation)
                if value["selected_entry"] is not None else None
            )
            if selected is None and now < intent.quote_deadline:
                return None
            with self.database.session() as session:
                begin_immediate(session)
                adapter = PerpetualExecutionAdapter(session)
                old = adapter.result(intent)
                if old is not None:
                    return old
                self._authority(session, intent)
                quote = require_prepared_bundle(session, preparation).quote
                now = aware_utc(self.clock())
                reason = None
                planned_loss_assessment = None
                if intent.phase == "entry" and condition_new_exposure_blocked_in_session(
                    session,
                    owner_id=intent.owner_id,
                    plan_id=intent.plan_id,
                    revision_ref=intent.revision.model_dump(mode="json"),
                ):
                    reason = "perpetual_condition_invalidated_new_exposure"
                elif entry_auth is not None and now >= aware_utc(
                    datetime.fromisoformat(entry_auth["latest_entry_at"])
                ):
                    reason = "entry_permission_elapsed"
                elif selected is None:
                    reason = "no_eligible_quote_before_deadline"
                else:
                    selected_entry, prepared = selected
                    book = quote.book
                    if (
                        book.observed_local_lower > intent.quote_deadline
                        or now > intent.quote_deadline
                        or now - book.observed_local_lower > timedelta(seconds=5)
                        or book.observed_local_upper > now
                    ):
                        reason = "first_eligible_quote_missed_or_expired"
                result = dict(
                    schema_version="perpetual-manual-execution-result.v1",
                    intent_id=intent.id,
                    status="no_fill" if reason else "filled",
                    reason=reason,
                    decided_at=now.isoformat(),
                    quote_selection_proof=proof,
                    fill_id=None,
                    ledger_revision=None,
                    episode_id=None,
                )
                if not reason:
                    ledger = account_auth["ledger"]
                    buy = (
                        (intent.target_side == "long")
                        if intent.phase == "entry"
                        else (intent.target_side == "short")
                    )
                    side = "buy" if buy else "sell"
                    with localcontext() as ctx:
                        ctx.prec = 100
                        price = execution_price_usdt(
                            quote.spec,
                            order_side=side,
                            bid=Decimal(book.bid),
                            ask=Decimal(book.ask),
                            slippage_bps=Decimal("2"),
                        )
                        if intent.phase == "entry":
                            qty = floor_contracts(
                                quote.spec,
                                Decimal(command.max_entry_notional_usdt)
                                / linear_base_quantity(quote.spec, Decimal(1))
                                / price,
                            )
                            stop, take = (
                                Decimal(command.stop_price_usdt),
                                Decimal(command.take_profit_price_usdt),
                            )
                            if not ((stop < price < take) if buy else (take < price < stop)):
                                reason = "first_quote_outside_user_exit_bounds"
                        else:
                            qty = abs(
                                Decimal(ledger["positions"][instrument_id]["signed_contracts"])
                            )
                        if qty <= 0:
                            reason = "user_amount_below_contract_minimum"
                        if not reason:
                            from ..decision.perpetual_accounting import paper_fill_contracts
                            from ..persistence.perpetual_paper_records import (
                                PerpetualPaperCapacityRecord,
                            )

                            capacity = session.get(
                                PerpetualPaperCapacityRecord,
                                (ledger["account_key"], book.capacity_key, side),
                                populate_existing=True,
                            )
                            available_qty = paper_fill_contracts(
                                quote.spec,
                                requested_contracts=qty,
                                opposing_size_contracts=Decimal(
                                    book.ask_contracts if buy else book.bid_contracts
                                ),
                                already_consumed_contracts=Decimal(capacity.consumed_contracts)
                                if capacity
                                else Decimal(0),
                            )
                            if not available_qty:
                                reason = "first_quote_capacity_exhausted"
                            elif (
                                intent.phase == "entry"
                                and entry_auth["planned_loss_limit_usdt"] is not None
                            ):
                                # available_qty is the same lot/depth/capacity result
                                # apply_fill will use under this writer lock/CAS.
                                planned_loss_assessment = self._planned_stop_loss(
                                    quote.spec,
                                    contracts=available_qty,
                                    entry_price=price,
                                    stop_price=Decimal(command.stop_price_usdt),
                                    target_side=intent.target_side,
                                    protocol=entry_auth["protocol"],
                                    limit=Decimal(entry_auth["planned_loss_limit_usdt"]),
                                )
                                if not planned_loss_assessment["within_limit"]:
                                    reason = "perp_manual_planned_loss_limit_exceeded"
                    if reason:
                        result.update(status="no_fill", reason=reason)
                    else:
                        updated = PerpetualPaperRepository(session).apply_fill(
                            prepared=prepared,
                            owner_id=intent.owner_id,
                            account_id=intent.account_id,
                            expected_version=ledger["state_version"],
                            request_id="manualfill_" + canonical_hash(intent.id),
                            phase=intent.phase,
                            order_side=side,
                            requested_contracts=qty,
                            fee_rate=Decimal("0.0005"),
                            slippage_bps=Decimal("2"),
                            cost_assumption_ref=intent.protocol_ref.ref,
                            now=now,
                        )
                        event = updated["event"]
                        previous = account_auth["episode"]
                        episode_id, head_version, old_body = self._episode_predecessor(
                            previous, intent
                        )
                        fill = dict(
                            schema_version="perpetual-manual-fill.v1",
                            id="perp_fill_" + canonical_hash(intent.id),
                            intent_id=intent.id,
                            episode_id=episode_id,
                            quote_capture_id=book.capture_id,
                            quote_capture_hash=book.capture_hash,
                            quote_capacity_key=book.capacity_key,
                            sampling_receipt=selected_entry["ref"],
                            protocol_ref=intent.protocol_ref.model_dump(mode="json"),
                            order_side=side,
                            filled_contracts=event["filled_contracts"],
                            fill_price_usdt=event["fill_price"],
                            filled_notional_usdt=number(
                                Decimal(event["base_quantity"]) * Decimal(event["fill_price"])
                            ),
                            trade_fee_usdt=event["trade_fee_usdt"],
                            gross_pnl_usdt=event["gross_pnl_usdt"],
                            ledger_parent=ledger["revision"],
                            ledger_revision=updated["revision"],
                            funding_cash_usdt=None,
                        )
                        fill_ref = adapter.append_fill(intent, fill, now)
                        remaining = number(
                            abs(Decimal(updated["positions"][instrument_id]["signed_contracts"]))
                        )
                        episode = dict(
                            old_body,
                            episode_id=episode_id,
                            version=head_version + 1,
                            owner_id=intent.owner_id,
                            account_id=intent.account_id,
                            entry_intent_id=old_body.get("entry_intent_id", intent.id),
                            command_json=intent.command_json,
                            remaining_contracts=remaining,
                            target_side=intent.target_side,
                            last_fill_ref=fill_ref,
                            ledger_revision=updated["revision"],
                            last_quote_capacity_key=book.capacity_key,
                            sampling_generation=selected_entry["body"]["generation"],
                            initial_trade_cash_usdt=old_body.get(
                                "initial_trade_cash_usdt", ledger["trade_cash_usdt"]
                            ),
                            gross_pnl_usdt=number(
                                Decimal(old_body.get("gross_pnl_usdt", "0"))
                                + Decimal(event["gross_pnl_usdt"])
                            ),
                            trade_fees_usdt=number(
                                Decimal(old_body.get("trade_fees_usdt", "0"))
                                + Decimal(event["trade_fee_usdt"])
                            ),
                            outcome_id=None,
                            entry_at=old_body.get("entry_at", now.isoformat()),
                            initial_ledger_revision=old_body.get(
                                "initial_ledger_revision", ledger["revision"]
                            ),
                            updated_at=now.isoformat(),
                        )
                        if planned_loss_assessment is not None:
                            episode["planned_loss_assessment"] = planned_loss_assessment
                        if intent.phase != "entry":
                            trigger = ActionRepository(session).get_command(
                                "system:perp-paper:v1", intent.trigger_ref.ref
                            )
                            episode.update(
                                exit_latched_reason=trigger.response["reason"],
                                first_exit_eligible_at=old_body.get(
                                    "first_exit_eligible_at", intent.eligible_at.isoformat()
                                ),
                            )
                            episode["exit_alarm"] = remaining != "0" and now >= (
                                aware_utc(datetime.fromisoformat(episode["first_exit_eligible_at"]))
                                + timedelta(seconds=30)
                            )
                        if remaining == "0":
                            episode["outcome_id"] = "perp_outcome_" + canonical_hash(episode_id)
                            episode["funding_status"] = "pending_coverage"
                        adapter.append_episode(
                            intent, episode, now=now, expected_version=head_version
                        )
                        result.update(
                            fill_id=fill["id"],
                            ledger_revision=updated["revision"],
                            episode_id=episode_id,
                        )
                        if remaining == "0":
                            revision = ActionRepository(session).get_revision(intent.revision.ref)
                            adapter.append_outcome(
                                intent,
                                dict(
                                    schema_version="perpetual-manual-outcome.v1",
                                    id=episode["outcome_id"],
                                    episode_id=episode_id,
                                    opportunity_id=revision.opportunity_id,
                                    status="unscorable",
                                    gross_pnl_usdt=episode["gross_pnl_usdt"],
                                    trade_fees_usdt=episode["trade_fees_usdt"],
                                    trade_cash_delta_usdt=number(
                                        Decimal(updated["trade_cash_usdt"])
                                        - Decimal(episode["initial_trade_cash_usdt"])
                                    ),
                                    funding_cash_usdt=None,
                                    net_pnl_usdt=None,
                                    ledger_revision=updated["revision"],
                                    gaps=[
                                        "funding_settlement_mark_and_holding_coverage_unavailable",
                                        "mark_risk_path_unavailable",
                                    ],
                                    authority="user_explicit_paper_simulation",
                                    native_reviewed=False,
                                    financial_qualified=False,
                                ),
                                now,
                            )
                if result["status"] == "no_fill" and intent.phase != "entry":
                    old_body = account_auth["episode"][0]
                    trigger = ActionRepository(session).get_command(
                        "system:perp-paper:v1", intent.trigger_ref.ref
                    )
                    first_exit = old_body.get(
                        "first_exit_eligible_at", intent.eligible_at.isoformat()
                    )
                    retained = dict(
                        old_body,
                        version=old_body["version"] + 1,
                        exit_latched_reason=trigger.response["reason"],
                        first_exit_eligible_at=first_exit,
                        exit_alarm=now
                        >= aware_utc(datetime.fromisoformat(first_exit)) + timedelta(seconds=30),
                        last_exit_gap=reason,
                        updated_at=now.isoformat(),
                        sampling_generation=(
                            selected[0]["body"]["generation"]
                            if selected
                            else (
                                observations[-1]["body"]["generation"]
                                if observations
                                else intent.sampling_generation
                            )
                        ),
                    )
                    adapter.append_episode(
                        intent, retained, now=now, expected_version=old_body["version"]
                    )
                adapter.append_result(intent, result, now)
                verify_prepared_bundle_after_writes(session, preparation)
                return result

    def read_execution(self, owner_id, intent_id):
        from ..persistence.action_execution_records import ExecutionFillRecord
        from ..persistence.action_records import ActionOutcomeRecord

        with self.database.session() as session:
            adapter = PerpetualExecutionAdapter(session)
            intent = adapter.intent(identity(intent_id))
            if intent.owner_id != identity(owner_id):
                raise PermissionError("perp_manual_execution_owner_invalid")
            receipt = adapter.result(intent)
            fill = None
            if receipt and receipt["fill_id"]:
                row = session.get(ExecutionFillRecord, receipt["fill_id"], populate_existing=True)
                _, fill = adapter.artifact(row, "perpetual-manual-fill.v1")
                original = PerpetualPaperRepository(session).revision(
                    fill["ledger_revision"]["ref"]
                )
                if original["revision"] != fill["ledger_revision"] or row.intent_id != intent.id:
                    raise ValueError("perp_manual_fill_ledger_join_invalid")
            found = adapter.episode(intent.account_key)
            episode = found[0] if found else None
            if (
                receipt
                and receipt.get("episode_id")
                and (episode is None or episode["episode_id"] != receipt["episode_id"])
            ):
                from sqlalchemy import select

                from ..persistence.action_execution_records import ExecutionEpisodeRecord

                row = session.scalar(
                    select(ExecutionEpisodeRecord)
                    .where(ExecutionEpisodeRecord.episode_id == receipt["episode_id"])
                    .order_by(ExecutionEpisodeRecord.version.desc())
                    .limit(1)
                )
                if row is None:
                    raise ValueError("perp_manual_episode_original_missing")
                _, episode = adapter.artifact(row, "perpetual-manual-episode.v1")
            outcome = None
            if episode and episode["outcome_id"]:
                row = session.get(
                    ActionOutcomeRecord, episode["outcome_id"], populate_existing=True
                )
                _, outcome = adapter.artifact(row, "perpetual-manual-outcome.v1")
            return dict(
                schema_version="perpetual-manual-execution-view.v1",
                owner_id=owner_id,
                intent=intent.model_dump(mode="json"),
                receipt=receipt,
                fill=fill,
                episode=episode,
                outcome=outcome,
            )

    @staticmethod
    def _provider_holding_interval(initial, current, repository):
        fills = []
        cursor = current
        while cursor["revision"] != initial["revision"]:
            event = cursor["event"]
            if event.get("kind") != "paper_fill" or cursor["parent"] is None:
                raise ValueError("perp_funding_episode_ledger_chain_invalid")
            receipt = event.get("clock_receipt")
            if (
                type(receipt) is not dict
                or receipt.get("schema_version") != "perpetual-book-clock.v1"
                or type(receipt.get("offset_lower_us")) is not int
                or type(receipt.get("offset_upper_us")) is not int
                or receipt["offset_lower_us"] > receipt["offset_upper_us"]
            ):
                raise ValueError("perp_funding_fill_clock_receipt_invalid")
            payload = {
                key: value for key, value in receipt.items() if key not in {"id", "payload_hash"}
            }
            digest = canonical_hash(payload)
            if receipt.get("payload_hash") != digest or receipt.get("id") != "perpclock_" + digest:
                raise ValueError("perp_funding_fill_clock_receipt_invalid")
            fills.append((event, aware_utc(datetime.fromisoformat(cursor["created_at"])), receipt))
            cursor = repository.revision(cursor["parent"]["ref"])
        fills.reverse()
        if (
            len(fills) < 2
            or fills[0][0].get("phase") != "entry"
            or fills[-1][0].get("phase") not in {"reduce", "exit"}
        ):
            raise ValueError("perp_funding_episode_fill_envelope_invalid")
        lower = fills[0][1] + timedelta(microseconds=fills[0][2]["offset_lower_us"])
        upper = fills[-1][1] + timedelta(microseconds=fills[-1][2]["offset_upper_us"])
        if lower >= upper:
            raise ValueError("perp_funding_episode_provider_interval_invalid")
        return lower, upper

    def reconcile_closed_episode(self, account_key, *, checkpoint=None):
        """Repair only a strictly proven zero-settlement closed holding interval."""
        if self.funding_history is None:
            raise ValueError("perp_funding_history_source_unavailable")

        from ..persistence.action_records import ActionOutcomeRecord

        def read(session):
            adapter = PerpetualExecutionAdapter(session)
            found = adapter.episode(account_key)
            if found is None:
                return None
            body, episode_ref = found
            if body["remaining_contracts"] != "0" or body.get(
                "funding_status", "pending_coverage"
            ) not in {"pending_coverage", "pending_source_retry"}:
                return None
            entry = adapter.intent(body["entry_intent_id"])
            self._authority(session, entry)
            repository = PerpetualPaperRepository(session)
            ledger = repository.current(entry.owner_id, entry.account_id)
            initial_ref = body.get("initial_ledger_revision")
            if type(initial_ref) is not dict or set(initial_ref) != {"ref", "hash"}:
                raise ValueError("perp_funding_legacy_initial_revision_missing")
            initial = repository.revision(initial_ref["ref"])
            if (
                ledger["revision"] != body["ledger_revision"]
                or initial["revision"] != body["initial_ledger_revision"]
                or ledger["funding_complete"]
                or any(Decimal(item["signed_contracts"]) for item in ledger["positions"].values())
            ):
                raise ValueError("perp_funding_episode_ledger_state_invalid")
            if not initial["funding_complete"]:
                raise ValueError("perp_funding_initial_balance_unknown")
            outcome_row = session.get(
                ActionOutcomeRecord, body["outcome_id"], populate_existing=True
            )
            if outcome_row is None:
                raise ValueError("perp_funding_episode_outcome_missing")
            _, outcome = adapter.artifact(outcome_row, "perpetual-manual-outcome.v1")
            if outcome["status"] != "unscorable" or outcome["funding_cash_usdt"] is not None:
                raise ValueError("perp_funding_episode_outcome_state_invalid")
            provider_lower, provider_upper = self._provider_holding_interval(
                initial, ledger, repository
            )
            return dict(
                episode=body,
                episode_ref=episode_ref,
                intent=entry.model_dump(mode="json"),
                ledger=ledger,
                initial=initial,
                outcome=outcome,
                provider_lower=provider_lower,
                provider_upper=provider_upper,
            )

        prepared = prepare_originals(self.database, read)
        if prepared.value is None:
            return None
        if checkpoint:
            checkpoint()
        value = prepared.value
        entry = ManualExecutionIntent.model_validate(value["intent"])
        command = parse_manual_execution(entry.command_json)
        inst_id = command.partition.instrument_id.removeprefix("okx:")
        import httpx

        try:
            capture_ids = asyncio.run(
                self.funding_history.collect_coverage(
                    inst_id=inst_id,
                    provider_lower=value["provider_lower"],
                    provider_upper=value["provider_upper"],
                )
            )
            cutoff = aware_utc(self.clock())
            coverage = self.funding_history.prepare_coverage(
                inst_id=inst_id,
                provider_lower=value["provider_lower"],
                provider_upper=value["provider_upper"],
                server_before_id=capture_ids[0],
                pages=capture_ids[1],
                server_after_id=capture_ids[2],
                as_of=cutoff,
            )
        except (httpx.HTTPError, OSError, TimeoutError) as error:
            raise ValueError("perp_funding_source_temporarily_unavailable") from error
        if checkpoint:
            checkpoint()
        now = aware_utc(self.clock())
        coverage_ref = coverage.value.reference.model_dump(mode="json")
        request_id = "perp_funding_" + canonical_hash([value["episode_ref"], coverage_ref])
        response = dict(
            schema_version="perpetual-funding-coverage-receipt.v1",
            episode_ref=value["episode_ref"],
            coverage_ref=coverage_ref,
            coverage=coverage.value.body,
            known_at=now.isoformat(),
        )
        receipt = make_command_receipt(
            owner_id="system:perp-paper:v1",
            request_id=request_id,
            plan_id=entry.plan_id,
            operation="perp_funding_coverage",
            payload_hash=coverage_ref["hash"],
            response=response,
            accepted_at=now,
        )
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            coverage.verify(session)
            ActionRepository(session).append_command_receipt(receipt)
            adapter = PerpetualExecutionAdapter(session)
            episode = dict(value["episode"])
            episode.update(
                version=episode["version"] + 1,
                funding_coverage_ref={"ref": receipt.request_id, "hash": receipt.receipt_hash},
                updated_at=now.isoformat(),
            )
            for key in (
                "funding_retry_count",
                "funding_retry_reason",
                "funding_next_retry_at",
            ):
                episode.pop(key, None)
            if coverage.value.settlements:
                episode.update(
                    funding_status="blocked_settlement_inputs",
                    funding_gaps=list(coverage.value.gaps),
                )
                adapter.append_episode(
                    entry,
                    episode,
                    now=now,
                    expected_version=value["episode"]["version"],
                )
                prepared.verify_after_writes(session)
                coverage.verify_after_writes(session)
                return {"status": "blocked", "coverage_ref": coverage_ref}
            updated = PerpetualPaperRepository(session).apply_zero_settlement_coverage(
                owner_id=entry.owner_id,
                account_id=entry.account_id,
                expected_version=value["ledger"]["state_version"],
                initial_revision=value["initial"]["revision"],
                episode_ref={
                    **value["episode_ref"],
                    "ledger_revision": value["ledger"]["revision"],
                },
                coverage_ref={"ref": receipt.request_id, "hash": receipt.receipt_hash},
                now=now,
            )
            outcome = dict(value["outcome"])
            outcome.update(
                id="perp_outcome_" + canonical_hash([episode["episode_id"], coverage_ref]),
                version=2,
                status="settled",
                funding_cash_usdt="0",
                net_pnl_usdt=outcome["trade_cash_delta_usdt"],
                ledger_revision=updated["revision"],
                funding_coverage_ref={"ref": receipt.request_id, "hash": receipt.receipt_hash},
                gaps=[gap for gap in outcome["gaps"] if not gap.startswith("funding_")],
            )
            episode.update(
                funding_status="complete_zero_settlements",
                ledger_revision=updated["revision"],
                outcome_id=outcome["id"],
                funding_gaps=[],
            )
            adapter.append_outcome(entry, outcome, now)
            settled_episode_ref = adapter.append_episode(
                entry,
                episode,
                now=now,
                expected_version=value["episode"]["version"],
            )
            from ..persistence.action_records import ActionOutcomeRecord

            settled_outcome = session.get(
                ActionOutcomeRecord, outcome["id"], populate_existing=True
            )
            settled_outcome_ref = {
                "ref": settled_outcome.outcome_id,
                "hash": settled_outcome.payload_hash,
            }
            prepared.verify_after_writes(session)
            coverage.verify_after_writes(session)
        # Cash settlement commits first. Historical learning is an independent
        # retryable derivative and cannot roll back or relabel a settled ledger.
        result = {"status": "reconciled", "coverage_ref": coverage_ref}
        try:
            from .perpetual_paper_experience import PerpetualPaperExperienceService

            experience = PerpetualPaperExperienceService(
                self.database, self.funding_history, clock=self.clock
            ).record(episode_ref=settled_episode_ref, outcome_ref=settled_outcome_ref)
            result.update(
                experience_status="recorded", experience_ref=experience.ref.model_dump(mode="json")
            )
        except Exception as error:
            result.update(
                experience_status="pending_retry",
                experience_error=type(error).__name__ + ":" + str(error),
            )
        return result

    def defer_closed_episode(self, account_key, reason, *, checkpoint=None):
        """Durably park a structural gap or back off one temporary source failure."""
        reason = (
            reason
            if type(reason) is str and reason.startswith("perp_") and len(reason) <= 128
            else "perp_funding_source_temporarily_unavailable"
        )
        permanent = reason in {
            "perp_funding_legacy_initial_revision_missing",
            "perp_funding_initial_balance_unknown",
            "perp_funding_episode_ledger_chain_invalid",
            "perp_funding_fill_clock_receipt_invalid",
            "perp_funding_episode_fill_envelope_invalid",
            "perp_funding_episode_provider_interval_invalid",
            "perp_funding_episode_ledger_state_invalid",
            "perp_funding_episode_outcome_missing",
            "perp_funding_episode_outcome_state_invalid",
        }

        def read(session):
            adapter = PerpetualExecutionAdapter(session)
            found = adapter.episode(account_key)
            if found is None:
                return None
            body, episode_ref = found
            if body["remaining_contracts"] != "0" or body.get(
                "funding_status", "pending_coverage"
            ) not in {"pending_coverage", "pending_source_retry"}:
                return None
            entry = adapter.intent(body["entry_intent_id"])
            self._authority(session, entry)
            return dict(
                episode=body,
                episode_ref=episode_ref,
                intent=entry.model_dump(mode="json"),
            )

        prepared = prepare_originals(self.database, read)
        if prepared.value is None:
            return None
        if checkpoint:
            checkpoint()
        value = prepared.value
        entry = ManualExecutionIntent.model_validate(value["intent"])
        now = aware_utc(self.clock())
        episode = dict(value["episode"])
        episode.update(
            version=episode["version"] + 1,
            updated_at=now.isoformat(),
            funding_retry_reason=reason,
        )
        if permanent:
            episode.update(
                funding_status="blocked_legacy_inputs",
                funding_gaps=[reason],
            )
            episode.pop("funding_next_retry_at", None)
            retry_at = None
        else:
            attempt = int(episode.get("funding_retry_count", 0)) + 1
            retry_at = now + timedelta(minutes=min(60, 5 * (2 ** min(attempt - 1, 4))))
            episode.update(
                funding_status="pending_source_retry",
                funding_retry_count=attempt,
                funding_next_retry_at=retry_at.isoformat(),
            )
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            PerpetualExecutionAdapter(session).append_episode(
                entry,
                episode,
                now=now,
                expected_version=value["episode"]["version"],
            )
            prepared.verify_after_writes(session)
        return {
            "status": "blocked" if permanent else "retry_scheduled",
            "reason": reason,
            "retry_at": retry_at.isoformat() if retry_at else None,
        }

    def observe_episode(self, account_key, *, checkpoint=None):
        from ..persistence.action_execution_records import (
            ExecutionIntentRecord,
            ExecutionReceiptRecord,
        )
        from .perpetual_quote_sampling import PerpetualQuoteSamplingAdapter

        def read(session):
            adapter = PerpetualExecutionAdapter(session)
            episode = adapter.episode(account_key)
            if episode is None or episode[0]["remaining_contracts"] == "0":
                return None
            body, episode_ref = episode
            entry = adapter.intent(body["entry_intent_id"])
            self._authority(session, entry)
            pending = session.scalar(
                select(ExecutionIntentRecord.intent_id)
                .outerjoin(
                    ExecutionReceiptRecord,
                    ExecutionReceiptRecord.intent_id == ExecutionIntentRecord.intent_id,
                )
                .where(
                    ExecutionIntentRecord.portfolio_id == account_key,
                    ExecutionReceiptRecord.intent_id.is_(None),
                )
                .limit(1)
            )
            if pending:
                return None
            ledger = PerpetualPaperRepository(session).current(entry.owner_id, entry.account_id)
            if ledger["revision"] != body["ledger_revision"]:
                raise ValueError("perp_manual_episode_ledger_head_changed")
            observations = PerpetualQuoteSamplingAdapter.observations(
                session, generation=body["sampling_generation"], scope_id=entry.scope_id
            )
            for observation in observations:
                captured = observation["body"]
                if captured["event"] == "captured":
                    if len(captured["capture_ids"]) != 2:
                        raise ValueError("perp_manual_bracket_original_required")
                    rows = [
                        session.get(RawCaptureRecord, k, populate_existing=True)
                        for k in captured["capture_ids"]
                    ]
                    if any(row is None for row in rows):
                        raise ValueError("perp_manual_sampling_capture_missing")
                    observation["cutoff"] = max(as_utc(row.recorded_at) for row in rows)
            head = session.get(QuoteSamplingHead, entry.scope_id, populate_existing=True)
            return dict(
                episode=body,
                episode_ref=episode_ref,
                intent=entry.model_dump(mode="json"),
                ledger=ledger,
                observations=observations,
                generation=head.generation if head else 0,
            )

        prepared = prepare_originals(self.database, read)
        if prepared.value is None:
            return None
        value = prepared.value
        entry = ManualExecutionIntent.model_validate(value["intent"])
        command = parse_manual_execution(entry.command_json)
        now = aware_utc(self.clock())
        reason = value["episode"].get("exit_latched_reason") or (
            "expiry" if now >= aware_utc(datetime.fromisoformat(command.exit_at)) else None
        )
        verified_generation = (
            value["observations"][-1]["body"]["generation"]
            if value["observations"]
            else value["episode"]["sampling_generation"]
        )
        trigger_source = None
        quote_preparations = []
        if reason is None:
            for observation in value["observations"]:
                if observation["body"]["event"] != "captured":
                    continue
                quote = self._quote(
                    observation, value["ledger"]["instrument_refs"][command.partition.instrument_id]
                )
                quote_preparations.append(quote)
                book = quote.value.book
                if (
                    now - book.observed_local_lower > timedelta(seconds=5)
                    or book.observed_local_upper > now
                ):
                    continue
                price = Decimal(book.bid if entry.target_side == "long" else book.ask)
                stop, take = (
                    Decimal(command.stop_price_usdt),
                    Decimal(command.take_profit_price_usdt),
                )
                stopped = (price <= stop) if entry.target_side == "long" else (price >= stop)
                target = (price >= take) if entry.target_side == "long" else (price <= take)
                if stopped or target:
                    reason = "stop" if stopped else "target"
                    trigger_source = dict(
                        sampling_receipt=observation["ref"],
                        capture_id=book.capture_id,
                        capture_hash=book.capture_hash,
                        executable_price_usdt=number(price),
                        price_basis="bid" if entry.target_side == "long" else "ask",
                        observed_local_lower=book.observed_local_lower.isoformat(),
                        observed_local_upper=book.observed_local_upper.isoformat(),
                    )
                    break
        if reason is None:
            if verified_generation == value["episode"]["sampling_generation"]:
                return None
            with self.database.session() as session:
                begin_immediate(session)
                prepared.verify(session)
                for quote in quote_preparations:
                    quote.verify(session)
                now = aware_utc(self.clock())
                episode = dict(
                    value["episode"],
                    version=value["episode"]["version"] + 1,
                    sampling_generation=verified_generation,
                    updated_at=now.isoformat(),
                )
                PerpetualExecutionAdapter(session).append_episode(
                    entry,
                    episode,
                    now=now,
                    expected_version=value["episode"]["version"],
                )
                prepared.verify_after_writes(session)
                for quote in quote_preparations:
                    quote.verify_after_writes(session)
            return None
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            for quote in quote_preparations:
                quote.verify(session)
            now = aware_utc(self.clock())
            trigger_id = "perp_trigger_" + canonical_hash([value["episode_ref"], reason])
            trigger = make_command_receipt(
                owner_id="system:perp-paper:v1",
                request_id=trigger_id,
                plan_id=entry.plan_id,
                operation="perp_exit_trigger",
                accepted_at=now,
                payload_hash=canonical_hash([value["episode_ref"], reason, trigger_source]),
                response=dict(
                    schema_version="perpetual-manual-exit-trigger.v1",
                    reason=reason,
                    episode_ref=value["episode_ref"],
                    entry_intent_id=entry.id,
                    source=trigger_source,
                    known_at=now.isoformat(),
                    exit_at=command.exit_at,
                ),
            )
            ActionRepository(session).append_command_receipt(trigger)
            eligible = now + timedelta(seconds=1)
            intent = seal(
                ManualExecutionIntent,
                dict(
                    entry.model_dump(mode="python"),
                    id="perp_exit_" + canonical_hash(value["episode_ref"]),
                    event_key="manualexit_" + canonical_hash(value["episode_ref"]),
                    phase="exit",
                    accepted_at=now,
                    eligible_at=eligible,
                    quote_deadline=eligible + timedelta(seconds=30),
                    sampling_generation=verified_generation,
                    episode_ref=value["episode_ref"],
                    trigger_ref={"ref": trigger.request_id, "hash": trigger.receipt_hash},
                ),
            )
            PerpetualExecutionAdapter(session).append_intent(intent)
            prepared.verify_after_writes(session)
            for quote in quote_preparations:
                quote.verify_after_writes(session)
            return intent
