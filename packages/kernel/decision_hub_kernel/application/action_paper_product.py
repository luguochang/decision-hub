"""Normal diagnostic registration and read models over the existing W6 engine.

No caller supplies a verified protocol, price or account. Registration uses an
authenticated frozen Task's method, and the ordinary execution tick opens it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from packages.contracts_py.decision_hub_contracts.generated.action_execution_v1 import (
    ActionExecutionProtocolV1,
    ActionOutcomeV2,
)

from ..decision.action_features import ACTION_MONITOR_MANIFEST_REF, action_feature_manifest
from ..decision.action_paper_accounting import Account, inherit
from ..decision.action_research_policy import research_action_catalog
from ..persistence.action_execution_records import (
    ExecutionEpisodeHeadRecord,
    ExecutionIntentRecord,
    ExecutionProtocolRecord,
    ExecutionReceiptRecord,
)
from ..persistence.action_execution_repository import (
    ExecutionRepository,
    account_values,
    canonical,
    identity_for,
    seal,
)
from ..persistence.action_paper_records import AccountHead
from ..persistence.action_records import DecisionOpportunityRecord
from ..persistence.action_repository import ActionRepository
from ..persistence.db import ProductCommandRecord, as_utc
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.action_paper_product import (
    Horizon,
    PaperMethodSource,
    PaperOpeningReceipt,
    PaperOptions,
    PaperRegistrationCommand,
    PaperRegistrationReceipt,
    PaperStatus,
)
from ..ports.market_path import canonical_hash, spot_scope
from .action_execution import ActionExecutionService
from .action_paper_opening import (
    OPENING_RULE_V2_HASH,
    OPENING_RULE_V2_REF,
    prepare_opening_in_session,
)
from .action_research_integration import REGISTRATION_ROLE, _body, _registration, _row, _task
from .research_run_scope import read_run_spot_admission_in_session

REGISTER = "action_paper_register"
OPENING = "action_paper_opening"


def _key(owner: str, request: str) -> str:
    if not owner or len(owner) > 128 or owner.strip() != owner or owner.startswith("system:"):
        raise ValueError("paper_owner_invalid")
    return "paper_" + canonical_hash([owner, request])


def _opening_key(receipt: PaperRegistrationReceipt) -> str:
    return "paper_open_" + canonical_hash([receipt.owner_id, receipt.request_id])


def _source(session: Session, opportunity_id: str) -> PaperMethodSource:
    value = ActionRepository(session).get_opportunity(opportunity_id)
    if value is None or value.cohort != "prospective" or not value.key.startswith("action-event:"):
        raise ValueError("paper_registered_live_method_required")
    prefix, run_id, horizon = value.key.split(":")
    originals = _registration(session, run_id)
    task = _task(session, run_id)
    row = _row(session, run_id, REGISTRATION_ROLE)
    admission = read_run_spot_admission_in_session(session, run_id)
    if row is None or admission is None or not any(item.id == value.id for item in originals):
        raise ValueError("paper_method_original_missing")
    recipe = _body(row)["recipe"]
    catalog = research_action_catalog(admission.scope.instrument)
    if (
        value.scope_id != admission.scope.scope_id
        or value.horizon != horizon
        or task.request.execution_mode != "live"
        or recipe["pack_definition_hash"] != task.pack_definition_hash
        or recipe["action_catalog_hash"] != canonical_hash(catalog)
        or value.recipe_hash != canonical_hash(recipe)
        or value.target_spec_hash != catalog["targets"][horizon]["hash"]
    ):
        raise ValueError("paper_frozen_method_scope_mismatch")
    return PaperMethodSource(
        opportunity_id=value.id,
        run_id=run_id,
        scope_id=value.scope_id,
        horizon=value.horizon,
        method_hash=value.recipe_hash,
        original_ref=row.output_id,
        original_hash=row.payload_hash,
    )


class ActionPaperProductService:
    def __init__(self, execution: ActionExecutionService):
        self.execution = execution
        self.database = execution.database

    def _read(self, session: Session, owner: str, request: str) -> PaperRegistrationReceipt:
        row = session.get(ProductCommandRecord, _key(owner, request), populate_existing=True)
        if row is None:
            raise ValueError("paper_registration_missing")
        receipt = PaperRegistrationReceipt.model_validate_json(row.result_json)
        payload = receipt.model_dump(mode="json")
        if (
            row.kind != REGISTER
            or row.owner_id != owner
            or receipt.owner_id != owner
            or receipt.request_id != request
            or row.target_id != receipt.protocol.id
            or row.payload_hash != receipt.command_hash
            or canonical(payload) != row.result_json
            or canonical_hash({k: v for k, v in payload.items() if k != "payload_hash"})
            != receipt.payload_hash
            or as_utc(row.created_at) != receipt.accepted_at
            or ExecutionRepository(session).protocol(receipt.protocol.id) != receipt.protocol
        ):
            raise ValueError("paper_registration_original_invalid")
        identity = identity_for(
            receipt.protocol,
            scope_id=receipt.source.scope_id,
            horizon=receipt.source.horizon,
            scenario=receipt.scenario,
            method_ref="A1",
            channel="shadow_engine",
            delay_seconds=60,
            owner_id=None,
        )
        if (
            identity.id != receipt.portfolio_id
            or identity.method_hash != receipt.source.method_hash
        ):
            raise ValueError("paper_registration_identity_mismatch")
        return receipt

    def receipt(self, owner: str, request: str) -> PaperRegistrationReceipt:
        with self.database.session() as session:
            return self._read(session, owner, request)

    def register(self, owner: str, command: PaperRegistrationCommand) -> PaperRegistrationReceipt:
        request_key = _key(owner, command.request_id)
        command_hash = canonical_hash(command.model_dump(mode="json"))
        with self.database.session() as session:
            if session.get(ProductCommandRecord, request_key) is not None:
                old = self._read(session, owner, command.request_id)
                if old.command_hash != command_hash:
                    raise ValueError("paper_registration_request_conflict")
                return old
        if not self.execution.enabled:
            raise ValueError("action_execution_disabled")

        def read_source(session: Session) -> PaperMethodSource:
            return _source(session, command.source_opportunity_id)

        prepared = prepare_originals(self.database, read_source)
        source = prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            if session.get(ProductCommandRecord, request_key) is not None:
                old = self._read(session, owner, command.request_id)
                if old.command_hash != command_hash:
                    raise ValueError("paper_registration_request_conflict")
                return old
            prepared.verify(session)
            now = self.execution._now()
            active_rows = session.scalars(
                select(ProductCommandRecord)
                .join(
                    ExecutionProtocolRecord,
                    ExecutionProtocolRecord.protocol_id == ProductCommandRecord.target_id,
                )
                .where(
                    ProductCommandRecord.owner_id == owner,
                    ProductCommandRecord.kind == REGISTER,
                    ExecutionProtocolRecord.end_at > now,
                    func.json_extract(ProductCommandRecord.result_json, "$.source.scope_id")
                    == source.scope_id,
                    func.json_extract(ProductCommandRecord.result_json, "$.source.horizon")
                    == source.horizon,
                    func.json_extract(ProductCommandRecord.result_json, "$.source.method_hash")
                    == source.method_hash,
                    func.json_extract(ProductCommandRecord.result_json, "$.scenario")
                    == command.scenario,
                )
                .order_by(ProductCommandRecord.created_at.desc())
                .limit(33)
            ).all()
            if len(active_rows) > 32:
                raise ValueError("paper_registration_history_budget_exceeded")
            for active_row in active_rows:
                previous = self._read(
                    session, owner, json.loads(active_row.result_json)["request_id"]
                )
                completed = self._bootstrap(session, previous)
                if completed is None or completed.status != "unknown":
                    raise ValueError("paper_active_registration_exists")
                if session.get(AccountHead, previous.portfolio_id) is not None:
                    raise ValueError("paper_unknown_opening_has_account")
            # Two complete minutes for prospective registration; never backfill.
            start = (now + timedelta(minutes=3)).replace(second=0, microsecond=0)
            end = start + timedelta(days=7)
            scope = spot_scope(source.scope_id.split(":")[3])
            catalog = research_action_catalog(scope.instrument)
            target = catalog["targets"][source.horizon]
            protocol = seal(
                ActionExecutionProtocolV1,
                dict(
                    schema_version="action-execution-protocol.v1",
                    id="paperproto_" + canonical_hash([owner, command.request_id]),
                    trial_id="papertrial_" + canonical_hash([owner, command.request_id]),
                    registered_at=now,
                    T0=start,
                    T1=end,
                    label_deadline=end + timedelta(hours=72),
                    cohort="diagnostic",
                    primary_horizon=source.horizon,
                    diagnostic_horizons=[],
                    scopes=[source.scope_id],
                    methods=[{"ref": "A1", "hash": source.method_hash}],
                    channels=["shadow_engine"],
                    delays=[60],
                    cost_policy_ref=catalog["cost_policy"]["ref"],
                    cost_policy_hash=catalog["cost_policy"]["hash"],
                    size_policy_ref=catalog["size_policy"]["ref"],
                    size_policy_hash=catalog["size_policy"]["hash"],
                    calculation_manifest_ref=ACTION_MONITOR_MANIFEST_REF,
                    calculation_manifest_hash=canonical_hash(
                        action_feature_manifest(scope.instrument)
                    ),
                    opening_rule_ref=OPENING_RULE_V2_REF,
                    opening_rule_hash=OPENING_RULE_V2_HASH,
                    targets=[
                        {
                            "horizon": source.horizon,
                            "target_spec_ref": target["ref"],
                            "target_spec_hash": target["hash"],
                        }
                    ],
                    initial_equity="10000",
                    unit="USDT",
                    paper_quantity_step="0.000000000001",
                ),
            )
            ExecutionRepository(session).register_protocol(protocol, now=now)
            identity = identity_for(
                protocol,
                scope_id=source.scope_id,
                horizon=source.horizon,
                scenario=command.scenario,
                method_ref="A1",
                channel="shadow_engine",
                delay_seconds=60,
                owner_id=None,
            )
            result = seal(
                PaperRegistrationReceipt,
                dict(
                    schema_version="action-paper-registration.v1",
                    owner_id=owner,
                    request_id=command.request_id,
                    command_hash=command_hash,
                    source=source,
                    scenario=command.scenario,
                    portfolio_id=identity.id,
                    protocol=protocol,
                    accepted_at=now,
                ),
            )
            session.add(
                ProductCommandRecord(
                    request_id=request_key,
                    owner_id=owner,
                    target_id=protocol.id,
                    kind=REGISTER,
                    payload_hash=command_hash,
                    result_json=canonical(result.model_dump(mode="json")),
                    created_at=now,
                )
            )
            session.flush()
            return self._read(session, owner, command.request_id)

    def options(self, owner: str, scope_id: str, horizon: Horizon) -> PaperOptions:
        _key(owner, "read")
        scope = spot_scope(scope_id.split(":")[3]) if len(scope_id.split(":")) == 6 else None
        if scope is None or scope.scope_id != scope_id or horizon not in {"30m", "24h", "72h"}:
            raise ValueError("paper_scope_invalid")
        with self.database.session() as session:
            latest = session.scalar(
                select(DecisionOpportunityRecord.opportunity_id)
                .where(
                    DecisionOpportunityRecord.scope_id == scope_id,
                    DecisionOpportunityRecord.horizon == horizon,
                    DecisionOpportunityRecord.cohort == "prospective",
                    DecisionOpportunityRecord.key.like("action-event:%"),
                )
                .order_by(
                    DecisionOpportunityRecord.registered_at.desc(),
                    DecisionOpportunityRecord.opportunity_id.desc(),
                )
                .limit(1)
            )
            source = _source(session, latest) if latest else None
            rows = session.scalars(
                select(ProductCommandRecord)
                .join(
                    ExecutionProtocolRecord,
                    ExecutionProtocolRecord.protocol_id == ProductCommandRecord.target_id,
                )
                .where(
                    ProductCommandRecord.owner_id == owner,
                    ProductCommandRecord.kind == REGISTER,
                    func.json_extract(ExecutionProtocolRecord.payload_json, "$.scopes[0]")
                    == scope_id,
                    func.json_extract(ExecutionProtocolRecord.payload_json, "$.primary_horizon")
                    == horizon,
                )
                .order_by(ProductCommandRecord.created_at.desc(), ProductCommandRecord.request_id)
                .limit(21)
            ).all()
            registrations = [
                self._read(session, owner, json.loads(row.result_json)["request_id"])
                for row in rows[:20]
            ]
            return PaperOptions(
                schema_version="action-paper-options.v1",
                owner_id=owner,
                scope_id=scope_id,
                horizon=horizon,
                enabled=self.execution.enabled,
                source=source,
                registrations=registrations,
                registrations_more=len(rows) > 20,
                active_request_ids=[
                    item.request_id
                    for item in registrations
                    if source is not None
                    and item.source.method_hash == source.method_hash
                    and item.protocol.T1 > self.execution._now()
                    and (
                        (opening := self._bootstrap(session, item)) is None
                        or opening.status != "unknown"
                    )
                ],
                gap_codes=[] if source else ["paper_registered_live_method_required"],
            )

    def _bootstrap(self, session: Session, registration: PaperRegistrationReceipt):
        row = session.get(ProductCommandRecord, _opening_key(registration), populate_existing=True)
        if row is None:
            return None
        value = PaperOpeningReceipt.model_validate_json(row.result_json)
        body = value.model_dump(mode="json")
        if (
            row.kind != OPENING
            or row.owner_id != registration.owner_id
            or row.target_id != registration.protocol.id
            or row.payload_hash != value.payload_hash
            or canonical(body) != row.result_json
            or value.registration_hash != registration.payload_hash
            or value.portfolio_id != registration.portfolio_id
            or canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
            != value.payload_hash
        ):
            raise ValueError("paper_bootstrap_original_invalid")
        return value

    def process_due(
        self, *, max_registrations: int = 4, checkpoint: Callable[[], None] | None = None
    ) -> int:
        if not self.execution.enabled:
            return 0
        if not 1 <= max_registrations <= 4:
            raise ValueError("paper_bootstrap_budget_invalid")
        now = self.execution._now()
        finished = aliased(ProductCommandRecord)
        with self.database.session() as session:
            rows = session.execute(
                select(ProductCommandRecord.owner_id, ProductCommandRecord.result_json)
                .join(
                    ExecutionProtocolRecord,
                    ExecutionProtocolRecord.protocol_id == ProductCommandRecord.target_id,
                )
                .outerjoin(
                    finished,
                    and_(
                        finished.target_id == ProductCommandRecord.target_id,
                        finished.kind == OPENING,
                    ),
                )
                .where(
                    ProductCommandRecord.kind == REGISTER,
                    finished.request_id.is_(None),
                    ExecutionProtocolRecord.start_at <= now,
                )
                .order_by(ExecutionProtocolRecord.start_at, ProductCommandRecord.request_id)
                .limit(max_registrations)
            ).all()
        processed = 0
        for owner, raw in rows:
            if checkpoint:
                checkpoint()
            registration = self.receipt(owner, json.loads(raw)["request_id"])
            account = None
            reason = None
            try:
                account = self.execution.register_portfolio(
                    registration.protocol.id,
                    scope_id=registration.source.scope_id,
                    horizon=registration.source.horizon,
                    scenario=registration.scenario,
                    method_ref="A1",
                )
            except ValueError as error:
                if str(error) == "execution_opening_T0_quote_pending":
                    continue
                if str(error) not in {
                    "execution_opening_T0_quote_missing",
                    "execution_registration_outside_window",
                }:
                    raise
                reason = str(error)
            with self.database.session() as session:
                begin_immediate(session)
                current = self._read(session, owner, registration.request_id)
                if current != registration:
                    raise ValueError("paper_registration_changed")
                if self._bootstrap(session, current) is not None:
                    continue
                # Recover account commit before opening receipt, including restart.
                if session.get(AccountHead, current.portfolio_id) is not None:
                    account = ExecutionRepository(session).account(current.portfolio_id)
                value = seal(
                    PaperOpeningReceipt,
                    dict(
                        schema_version="action-paper-bootstrap.v1",
                        registration_hash=current.payload_hash,
                        portfolio_id=current.portfolio_id,
                        status="opened" if account else "unknown",
                        reason=None if account else reason,
                        checkpoint_ref=account.id if account else None,
                        checkpoint_hash=account.payload_hash if account else None,
                        decided_at=self.execution._now(),
                    ),
                )
                session.add(
                    ProductCommandRecord(
                        request_id=_opening_key(current),
                        owner_id=owner,
                        target_id=current.protocol.id,
                        kind=OPENING,
                        payload_hash=value.payload_hash,
                        result_json=canonical(value.model_dump(mode="json")),
                        created_at=value.decided_at,
                    )
                )
            processed += 1
        return processed

    def outcome(self, owner: str, request: str, outcome_id: str) -> dict[str, object]:
        registration = self.receipt(owner, request)
        # Check indexed ownership before expensive original traversal.
        with self.database.session() as session:
            row = session.scalar(
                select(ExecutionReceiptRecord)
                .where(
                    ExecutionReceiptRecord.portfolio_id == registration.portfolio_id,
                    func.json_extract(ExecutionReceiptRecord.payload_json, "$.outcome_ref")
                    == outcome_id,
                )
                .limit(1)
            )
            if row is None:
                raise ValueError("paper_outcome_missing")
        value = self.execution.read_outcome(outcome_id)
        if value["outcome"].portfolio_id != registration.portfolio_id:
            raise ValueError("paper_outcome_portfolio_mismatch")
        return value

    def opportunities(
        self, owner: str, request: str, *, after: str | None = None
    ) -> dict[str, object]:
        registration = self.receipt(owner, request)
        return self.execution.opportunities(registration.portfolio_id, after=after, limit=20)

    def outcomes(self, owner: str, request: str, *, after: str | None = None) -> dict[str, object]:
        registration = self.receipt(owner, request)
        with self.database.session() as session:
            query = select(ExecutionReceiptRecord).where(
                ExecutionReceiptRecord.portfolio_id == registration.portfolio_id,
            )
            if after:
                if session.scalar(query.where(ExecutionReceiptRecord.receipt_id == after)) is None:
                    raise ValueError("paper_outcome_cursor_mismatch")
                query = query.where(ExecutionReceiptRecord.receipt_id > after)
            rows = session.scalars(query.order_by(ExecutionReceiptRecord.receipt_id).limit(6)).all()
            originals = [ExecutionRepository(session).result(row.intent_id) for row in rows[:5]]
        values = [self.outcome(owner, request, item.outcome_ref) for item in originals]
        return dict(
            portfolio_id=registration.portfolio_id,
            items=[
                dict(outcome=value["outcome"], receipt=value["execution_receipt"])
                for value in values
            ],
            next_cursor=rows[4].receipt_id if len(rows) > 5 else None,
            financial_qualified=False,
        )

    def status(self, owner: str, request: str) -> PaperStatus:
        now = self.execution._now()
        with self.database.session() as session:
            registration = self._read(session, owner, request)
            bootstrap = self._bootstrap(session, registration)
            repository = ExecutionRepository(session)
            account = (
                repository.account(registration.portfolio_id)
                if session.get(AccountHead, registration.portfolio_id)
                else None
            )
            if account is not None and account.created_at > now:
                raise ValueError("paper_account_from_future")
            episode_head = session.get(ExecutionEpisodeHeadRecord, registration.portfolio_id)
            episode = (
                repository.episode(
                    f"{episode_head.episode_id}:v{episode_head.version}", episode_head.payload_hash
                )
                if episode_head
                else None
            )
            pending = (
                session.scalar(
                    select(func.count())
                    .select_from(ExecutionIntentRecord)
                    .outerjoin(
                        ExecutionReceiptRecord,
                        ExecutionReceiptRecord.intent_id == ExecutionIntentRecord.intent_id,
                    )
                    .where(
                        ExecutionIntentRecord.portfolio_id == registration.portfolio_id,
                        ExecutionReceiptRecord.intent_id.is_(None),
                    )
                )
                or 0
            )
            results_query = select(ExecutionReceiptRecord).where(
                ExecutionReceiptRecord.portfolio_id == registration.portfolio_id
            )
            count = session.scalar(select(func.count()).select_from(results_query.subquery())) or 0
            rows = session.scalars(
                results_query.order_by(
                    ExecutionReceiptRecord.decided_at.desc(), ExecutionReceiptRecord.receipt_id
                ).limit(3)
            ).all()
            results = [repository.result(row.intent_id) for row in rows]
            if any(item is None for item in results):
                raise ValueError("paper_execution_receipt_missing")
            outcome_ids = [item.outcome_ref for item in results]
            account_result = (
                repository.result(account.execution_key)
                if account and account.version > 1
                else None
            )
            denominator = (
                session.scalar(
                    select(func.count())
                    .select_from(DecisionOpportunityRecord)
                    .where(
                        DecisionOpportunityRecord.scope_id == registration.source.scope_id,
                        DecisionOpportunityRecord.horizon == registration.source.horizon,
                        DecisionOpportunityRecord.recipe_hash == registration.source.method_hash,
                        DecisionOpportunityRecord.target_hash
                        == registration.protocol.targets[0].target_spec_hash,
                        DecisionOpportunityRecord.registered_at >= registration.protocol.T0,
                        DecisionOpportunityRecord.registered_at < registration.protocol.T1,
                    )
                )
                or 0
            )
            if account and account.version == 1:
                opening = (
                    prepare_opening_in_session(
                        session,
                        protocol=registration.protocol,
                        scope=spot_scope(registration.source.scope_id.split(":")[3]),
                        archive=self.execution.store.archive,
                        observed_at=now,
                    )
                    if registration.scenario == "existing_long"
                    else None
                )
                expected = (
                    inherit(
                        registration.source.scope_id,
                        equity=Decimal("10000"),
                        reference_price=opening.reference_price,
                    )
                    if opening
                    else Account(registration.source.scope_id, Decimal("10000"))
                )
                if account.account_after.model_dump() != account_values(expected):
                    raise ValueError("paper_opening_account_mismatch")
        outcomes = [
            cast(ActionOutcomeV2, self.outcome(owner, request, item)["outcome"])
            for item in outcome_ids
        ]
        if account and account.version > 1:
            if account_result is None or (
                account_result.checkpoint_ref,
                account_result.checkpoint_hash,
            ) != (account.id, account.payload_hash):
                raise ValueError("paper_account_execution_receipt_missing")
            if account_result.outcome_ref not in outcome_ids:
                self.outcome(owner, request, account_result.outcome_ref)
        state = (
            "running"
            if account
            else "unknown"
            if bootstrap
            else "waiting_start"
            if now < registration.protocol.T0
            else "waiting_opening"
        )
        if account and now >= registration.protocol.T1:
            state = "ended"
        return PaperStatus(
            schema_version="action-paper-status.v1",
            registration=registration,
            bootstrap=bootstrap,
            state=state,
            account=account,
            episode=episode,
            pending_intents=pending,
            outcomes_count=count,
            outcomes=outcomes,
            opportunities_count=denominator,
            execution_reasons={item.outcome_ref: item.reason for item in results},
            unresolved_inventory=bool(
                account
                and Decimal(account.account_after.quantity) > 0
                and (episode is None or episode.status == "unpriced")
            ),
            financial_qualified=False,
            observed_at=now,
        )
