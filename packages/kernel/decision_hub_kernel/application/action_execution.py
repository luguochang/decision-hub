"""Artifact-only authenticated paper execution service; no broker or model calls."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from sqlalchemy import select, tuple_, update

from packages.contracts_py.decision_hub_contracts.generated.action_execution_v1 import (
    ActionExecutionEpisodeV1,
    ActionExecutionIntentV1,
    ActionExecutionProtocolV1,
    ActionExecutionReceiptV1,
    ActionFillV1,
    ActionFillV2,
    ActionOutcomeV2,
    ActionPaperCheckpointV1,
)
from packages.kernel.decision_hub_kernel.decision.action_execution_clock import (
    ExecutionWindow,
    execution_window,
    exit_trigger_deadline,
    validate_selected_interval,
    validate_selected_quote,
)
from packages.kernel.decision_hub_kernel.decision.action_features import (
    ACTION_MONITOR_MANIFEST_REF,
    action_feature_manifest,
)
from packages.kernel.decision_hub_kernel.decision.action_paper_accounting import (
    Account,
    Quote,
    buy,
    inherit,
    marked_equity,
    reduce_quantity,
    sell,
    size_entry,
)
from packages.kernel.decision_hub_kernel.decision.action_research_policy import (
    research_action_catalog,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    ExecutionEpisodeHeadRecord,
    ExecutionIntentRecord,
    ExecutionReceiptRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
    ExecutionRepository,
    account_for,
    account_values,
    canonical,
    check_fill,
    episode_ref,
    identity_for,
    number,
    seal,
)
from packages.kernel.decision_hub_kernel.persistence.action_quote_repository import (
    AvailabilityRecord,
    QuoteRecord,
    QuoteRepository,
    StoredQuote,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    begin_immediate,
    prepare_originals,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    aware_utc,
    canonical_hash,
    spot_scope,
)

from .action_execution_originals import (
    EvaluationOriginals,
    PublicationOriginals,
    read_evaluation_in_session,
    read_publication_in_session,
)
from .action_paper_opening import quote_clock_policy


def _fresh_quote(quote: StoredQuote, protocol: ActionExecutionProtocolV1, at: datetime) -> bool:
    policy = quote_clock_policy(protocol)
    try:
        lower, upper = quote.parsed.local_interval(clock_policy=policy)
    except ValueError:
        return False
    return upper <= at and at - lower <= timedelta(seconds=10)


def _validate_quote_for_intent(
    quote: StoredQuote,
    protocol: ActionExecutionProtocolV1,
    intent: ActionExecutionIntentV1,
    at: datetime,
) -> None:
    window = ExecutionWindow(
        intent.base_action_at,
        intent.eligible_at,
        intent.quote_deadline,
        intent.channel,
        intent.delay_seconds,
    )
    policy = quote_clock_policy(protocol)
    lower, upper = quote.parsed.local_interval(clock_policy=policy)
    common: dict[str, Any] = dict(
        window=window,
        received_at=quote.parsed.capture.completed_at,
        available_at=quote.available_at,
        executed_at=at,
        entry_valid_until=intent.entry_valid_until,
    )
    if policy == "legacy":
        validate_selected_quote(observed_at=quote.parsed.observed_at, **common)
    else:
        validate_selected_interval(
            observed_local_lower=lower,
            observed_local_upper=upper,
            capture_started_at=quote.parsed.capture.started_at,
            **common,
        )


def _verify_fill_quote(
    fill: ActionFillV1 | ActionFillV2,
    quote: StoredQuote,
    protocol: ActionExecutionProtocolV1,
) -> None:
    if (fill.provider_observed_at, fill.received_at, fill.available_at) != (
        quote.parsed.observed_at,
        quote.parsed.capture.completed_at,
        quote.available_at,
    ):
        raise ValueError("execution_fill_quote_clock_mismatch")
    if quote_clock_policy(protocol) == "bracket_v1":
        clock = quote.parsed.clock_receipt
        if not isinstance(fill, ActionFillV2) or clock is None:
            raise ValueError("execution_fill_clock_version_mismatch")
        if (
            fill.clock_ref,
            fill.clock_hash,
            fill.observed_local_lower,
            fill.observed_local_upper,
        ) != (
            clock["id"],
            clock["payload_hash"],
            *quote.parsed.local_interval(clock_policy="bracket_v1"),
        ):
            raise ValueError("execution_fill_clock_original_mismatch")
    elif not isinstance(fill, ActionFillV1):
        raise ValueError("execution_fill_clock_version_mismatch")


def body_for(cls, **values):
    return {field.alias or key: None for key, field in cls.model_fields.items()} | values


def _catalog(protocol, identity):
    scope = spot_scope(identity.scope_id.split(":")[3])
    catalog = research_action_catalog(scope.instrument)
    if scope.scope_id != identity.scope_id or protocol.scopes != [scope.scope_id]:
        raise ValueError("execution_protocol_scope_mismatch")
    if (protocol.initial_equity, protocol.unit, protocol.paper_quantity_step) != (
        "10000",
        "USDT",
        "0.000000000001",
    ):
        raise ValueError("execution_registered_capital_policy_mismatch")
    manifest = canonical_hash(action_feature_manifest(scope.instrument))
    target = catalog["targets"][identity.horizon]
    if (
        protocol.size_policy_ref,
        protocol.size_policy_hash,
        protocol.cost_policy_ref,
        protocol.cost_policy_hash,
        protocol.calculation_manifest_ref,
        protocol.calculation_manifest_hash,
        identity.target_spec_hash,
    ) != (
        catalog["size_policy"]["ref"],
        catalog["size_policy"]["hash"],
        catalog["cost_policy"]["ref"],
        catalog["cost_policy"]["hash"],
        ACTION_MONITOR_MANIFEST_REF,
        manifest,
        target["hash"],
    ):
        raise ValueError("execution_registered_policy_mismatch")
    return scope, catalog


class _ArchiveReadSet:
    def __init__(self, archive):
        self.archive = archive
        self.originals = {}

    def read(self, ref):
        raw = self.archive.read(ref)
        hashed = hashlib.sha256(raw).hexdigest()
        if ref in self.originals and self.originals[ref] != hashed:
            raise ValueError("execution_quote_original_changed")
        self.originals[ref] = hashed
        return raw

    def verify(self):
        for ref, digest in self.originals.items():
            if hashlib.sha256(self.archive.read(ref)).hexdigest() != digest:
                raise ValueError("execution_quote_original_changed")


@dataclass(frozen=True)
class _Observed:
    protocol: ActionExecutionProtocolV1
    portfolio: ActionPaperCheckpointV1
    publication: PublicationOriginals
    evaluation: EvaluationOriginals
    catalog: dict[str, Any]
    episode: ActionExecutionEpisodeV1 | None


class ActionExecutionService:
    def __init__(
        self,
        database: Database,
        *,
        market_store,
        feature_root: Path,
        clock: Callable[[], datetime] = utcnow,
        enabled: bool = False,
        perpetual_execution=None,
    ):
        self.database = database
        self.store = market_store
        self.feature_root = feature_root
        self.clock = clock
        self.enabled = enabled
        self.perpetual_execution = perpetual_execution

    def _now(self):
        return aware_utc(self.clock())

    def register_portfolio(
        self,
        protocol_id: str,
        *,
        scope_id: str,
        horizon: str,
        scenario: str,
        method_ref: str,
        channel: str = "shadow_engine",
        delay_seconds: int = 60,
        owner_id: str | None = None,
    ):
        """Register exactly the precommitted diagnostic capital; never per Plan."""
        if not self.enabled:
            raise ValueError("action_execution_disabled")
        archive = _ArchiveReadSet(self.store.archive)

        def read(session):
            repository = ExecutionRepository(session)
            protocol = repository.protocol(protocol_id)
            identity = identity_for(
                protocol,
                scope_id=scope_id,
                horizon=horizon,
                scenario=scenario,
                method_ref=method_ref,
                channel=channel,
                delay_seconds=delay_seconds,
                owner_id=owner_id,
            )
            scope, _ = _catalog(protocol, identity)
            opening = None
            if scenario == "existing_long":
                from packages.kernel.decision_hub_kernel.application.action_paper_opening import (
                    prepare_opening_in_session,
                )

                opening = prepare_opening_in_session(
                    session,
                    protocol=protocol,
                    scope=scope,
                    archive=archive,
                    observed_at=self._now(),
                )
            return protocol, identity, opening

        prepared = prepare_originals(self.database, read)
        protocol, identity, opening = prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            repository = ExecutionRepository(session)
            from packages.kernel.decision_hub_kernel.persistence.action_paper_records import (
                AccountHead,
            )

            if session.get(AccountHead, identity.id):
                return repository.account(identity.id)
            prepared.verify(session)
            archive.verify()
            now = self._now()
            if not protocol.T0 <= now < protocol.T1:
                raise ValueError("execution_registration_outside_window")
            refs = [{"ref": "protocol:" + protocol.id, "hash": protocol.payload_hash}]
            if opening is not None:
                from packages.kernel.decision_hub_kernel.application.action_paper_opening import (
                    store_opening_in_session,
                )

                store_opening_in_session(session, opening)
                account = inherit(
                    scope_id,
                    equity=Decimal(protocol.initial_equity),
                    reference_price=opening.reference_price,
                )
                refs.append({"ref": opening.id, "hash": opening.payload_hash})
            else:
                account = Account(scope_id, Decimal(protocol.initial_equity))
            checkpoint = seal(
                ActionPaperCheckpointV1,
                body_for(
                    ActionPaperCheckpointV1,
                    schema_version="action-paper-checkpoint.v1",
                    id="checkpoint_" + canonical_hash([identity.id, 1]),
                    portfolio_identity=identity.model_dump(mode="json"),
                    portfolio_id=identity.id,
                    identity_hash=identity.identity_hash,
                    version=1,
                    execution_key="opening:" + protocol.id,
                    request_hash=canonical_hash([protocol.payload_hash, identity.identity_hash]),
                    created_at=now,
                    account_after=account_values(account),
                    event_kind="opening",
                    opening_original_refs=refs,
                    disposition="opened",
                    exclusions=[],
                ),
            )
            repository.append_checkpoint(checkpoint)
            return checkpoint

    def observe_evaluation(
        self,
        portfolio_id: str,
        *,
        plan_id: str,
        revision_id: str,
        evaluation_id: str,
        checkpoint=None,
    ):
        if not self.enabled:
            raise ValueError("action_execution_disabled")
        key = "condition:" + evaluation_id
        original_id = "intent_" + canonical_hash([portfolio_id, key])
        with self.database.session() as session:
            old = (
                ExecutionRepository(session).intent(original_id)
                if session.get(ExecutionIntentRecord, original_id)
                else None
            )
            if old:
                if (old.plan_id, old.revision_id) != (plan_id, revision_id):
                    raise ValueError("execution_evaluation_retry_identity_mismatch")
                return old
        observed = self._now()

        def read(session):
            repository = ExecutionRepository(session)
            portfolio = repository.account(portfolio_id)
            identity = portfolio.portfolio_identity
            protocol = repository.protocol(identity.protocol_ref)
            _, catalog = _catalog(protocol, identity)
            evaluation = read_evaluation_in_session(
                session,
                plan_id=plan_id,
                revision_id=revision_id,
                evaluation_id=evaluation_id,
                store=self.store,
                feature_root=self.feature_root,
                observed_at=observed,
                checkpoint=checkpoint,
            )
            publication = read_publication_in_session(
                session,
                revision=evaluation.revision,
                observed_at=observed,
                channel=identity.channel,
                owner_id=identity.owner_id,
            )
            if (
                identity.scope_id,
                identity.horizon,
                identity.target_spec_hash,
                identity.method_hash,
            ) != (
                evaluation.revision.scope_id,
                evaluation.revision.horizon,
                publication.opportunity.target_spec_hash,
                publication.opportunity.recipe_hash,
            ):
                raise ValueError("execution_opportunity_frozen_method_mismatch")
            if not protocol.T0 <= evaluation.evaluation.evaluated_at < protocol.T1:
                raise ValueError("execution_trigger_outside_registered_window")
            if identity.channel == "pc_visible" and publication.display is None:
                raise ValueError("execution_exact_owner_display_required")
            episode = repository.open_episode(portfolio_id)
            if evaluation.phase == "entry" and (
                episode is not None or account_for(portfolio).quantity > 0
            ):
                raise ValueError("execution_open_episode_forbids_new_entry")
            if evaluation.phase != "entry" and episode is None:
                raise ValueError("execution_position_episode_required")
            return _Observed(protocol, portfolio, publication, evaluation, catalog, episode)

        prepared = prepare_originals(self.database, read)
        value = prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            repository = ExecutionRepository(session)
            old = (
                repository.intent(original_id)
                if session.get(ExecutionIntentRecord, original_id)
                else None
            )
            if old:
                return old
            exclusive_key = key
            if value.evaluation.phase == "exit":
                if value.episode is None:
                    raise ValueError("execution_position_episode_required")
                exclusive_key = "episode-exit:" + value.episode.id
                previous_exit = repository.intent_by_key(portfolio_id, exclusive_key)
                if previous_exit:
                    raise ValueError("execution_episode_exit_already_issued")
            prepared.verify(session)
            value.evaluation.verify_bytes(self.store)
            now = self._now()
            head = ActionRepository(session).get_head(plan_id)
            if head is None or head.current_revision_id != revision_id:
                raise ValueError("execution_revision_changed")
            if value.evaluation.phase == "entry" and (
                head.state != "triggered" or head.monitor_state != "active"
            ):
                raise ValueError("execution_entry_no_longer_active")
            current = repository.account(portfolio_id)
            if current.payload_hash != value.portfolio.payload_hash:
                raise ValueError("execution_account_changed_after_read")
            risk = value.evaluation.risk
            if risk:
                u_ref = "execution_u_" + risk.payload_hash
                row = session.get(RunRoleOutputRecord, u_ref, populate_existing=True)
                if row and (
                    row.payload_hash != risk.payload_hash or row.payload_json != risk.payload_json
                ):
                    raise ValueError("execution_u_original_collision")
                if row is None:
                    session.add(
                        RunRoleOutputRecord(
                            output_id=u_ref,
                            run_id=value.publication.decision.run_id,
                            role="action_execution_u",
                            schema_version="action-execution-risk-basis.v1",
                            payload_json=risk.payload_json,
                            payload_hash=risk.payload_hash,
                            created_at=now,
                        )
                    )
            else:
                if value.episode is None:
                    raise ValueError("execution_position_episode_required")
                u_ref = value.episode.U_basis_ref
                row = session.get(RunRoleOutputRecord, u_ref, populate_existing=True)
                if (
                    row is None
                    or row.payload_hash != value.episode.U_basis_hash
                    or canonical_hash(json.loads(row.payload_json)) != row.payload_hash
                ):
                    raise ValueError("execution_episode_u_original_missing")
                payload = json.loads(row.payload_json)
                from .action_execution_originals import (
                    ExecutionU,
                )

                risk = ExecutionU(
                    Decimal(payload["value"]),
                    payload["unit"],
                    datetime.fromisoformat(payload["measurement_end"]),
                    datetime.fromisoformat(payload["knowledge_cutoff"]),
                    datetime.fromisoformat(payload["known_at"]),
                    row.payload_json,
                    row.payload_hash,
                )
            intent = self._intent(value, now, key, u_ref, risk)
            if value.evaluation.phase == "exit":
                intent = seal(
                    ActionExecutionIntentV1,
                    intent.model_dump(mode="python") | {"event_key": exclusive_key},
                )
            repository.append_intent(intent)
            return intent

    def activate_inherited(
        self,
        portfolio_id: str,
        *,
        plan_id: str,
        revision_id: str,
        evaluation_id: str,
        checkpoint=None,
    ):
        """Attach actual protection to existing T0 inventory, never an entry fill."""
        from packages.kernel.decision_hub_kernel.application.action_execution_inherited import (
            activation_payload,
            activation_ref,
            derive_inherited_episode,
        )
        from packages.kernel.decision_hub_kernel.application.action_paper_opening import (
            prepare_opening_in_session,
        )

        from .action_execution_originals import (
            read_activation_in_session,
        )

        if not self.enabled:
            raise ValueError("action_execution_disabled")
        observed = self._now()
        archive = _ArchiveReadSet(self.store.archive)

        def read(session):
            repository = ExecutionRepository(session)
            account = repository.account(portfolio_id)
            identity = account.portfolio_identity
            protocol = repository.protocol(identity.protocol_ref)
            scope, _ = _catalog(protocol, identity)
            existing = repository.open_episode(portfolio_id)
            if existing:
                return existing
            if identity.scenario != "existing_long" or account.version != 1:
                raise ValueError("execution_inherited_initial_protection_required")
            opening = prepare_opening_in_session(
                session, protocol=protocol, scope=scope, archive=archive, observed_at=observed
            )
            original = read_activation_in_session(
                session,
                plan_id=plan_id,
                revision_id=revision_id,
                evaluation_id=evaluation_id,
                store=self.store,
                feature_root=self.feature_root,
                observed_at=observed,
                checkpoint=checkpoint,
            )
            pub = read_publication_in_session(
                session,
                revision=original.revision,
                observed_at=observed,
                channel=identity.channel,
                owner_id=identity.owner_id,
            )
            if (
                identity.scope_id,
                identity.horizon,
                identity.target_spec_hash,
                identity.method_hash,
            ) != (
                original.revision.scope_id,
                original.revision.horizon,
                pub.opportunity.target_spec_hash,
                pub.opportunity.recipe_hash,
            ):
                raise ValueError("execution_opportunity_frozen_method_mismatch")
            if not protocol.T0 <= original.evaluation.evaluated_at < protocol.T1:
                raise ValueError("execution_trigger_outside_registered_window")
            if identity.channel == "pc_visible" and pub.display is None:
                raise ValueError("execution_exact_owner_display_required")
            return account, opening, original, pub

        prepared = prepare_originals(self.database, read)
        if isinstance(prepared.value, ActionExecutionEpisodeV1):
            return prepared.value
        account, opening, original, pub = prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            repository = ExecutionRepository(session)
            existing = repository.open_episode(portfolio_id)
            if existing:
                return existing
            prepared.verify(session)
            archive.verify()
            original.verify_bytes(self.store)
            now = self._now()
            head = ActionRepository(session).get_head(plan_id)
            if (
                head is None
                or head.current_revision_id != revision_id
                or head.monitor_state != "active"
                or head.state not in {"armed", "triggered"}
            ):
                raise ValueError("execution_inherited_plan_no_longer_active")
            if repository.account(portfolio_id).payload_hash != account.payload_hash:
                raise ValueError("execution_account_changed_after_read")
            risk = original.risk
            if risk is None:
                raise ValueError("execution_authenticated_u_required")
            episode = derive_inherited_episode(
                opening=opening,
                checkpoint=account,
                revision=original.revision,
                opportunity=pub.opportunity,
                risk=risk,
                activated_at=now,
            )
            row = session.get(RunRoleOutputRecord, episode.U_basis_ref, populate_existing=True)
            if row and (
                row.payload_hash != risk.payload_hash or row.payload_json != risk.payload_json
            ):
                raise ValueError("execution_u_original_collision")
            if row is None:
                session.add(
                    RunRoleOutputRecord(
                        output_id=episode.U_basis_ref,
                        run_id=pub.decision.run_id,
                        role="action_execution_u",
                        schema_version="action-execution-risk-basis.v1",
                        payload_json=risk.payload_json,
                        payload_hash=risk.payload_hash,
                        created_at=now,
                    )
                )
            body = activation_payload(
                episode=episode, original=original, publication=pub, opening=opening
            )
            session.add(
                RunRoleOutputRecord(
                    output_id=activation_ref(episode.id),
                    run_id=pub.decision.run_id,
                    role="action_inherited_activation",
                    schema_version=body["schema_version"],
                    payload_json=canonical(body),
                    payload_hash=canonical_hash(body),
                    created_at=now,
                )
            )
            repository.append_episode(episode, next_due_at=now)
            return episode

    def _episode_originals(self, session, episode, archive, observed, checkpoint=None):
        repository = ExecutionRepository(session)
        if episode.opening_kind == "inherited":
            from packages.kernel.decision_hub_kernel.application.action_execution_inherited import (
                read_inherited_in_session,
            )

            return read_inherited_in_session(
                session,
                episode=episode,
                store=self.store,
                feature_root=self.feature_root,
                archive=archive,
                observed_at=observed,
                checkpoint=checkpoint,
            )
        entry = repository.intent(episode.entry_intent_ref.removeprefix("intent:"))
        result = repository.result(entry.id)
        if result is None or result.status != "filled" or result.fill_ref != episode.entry_fill_ref:
            raise ValueError("execution_episode_entry_receipt_required")
        original = read_evaluation_in_session(
            session,
            plan_id=entry.plan_id,
            revision_id=entry.revision_id,
            evaluation_id=entry.evaluation_ref,
            store=self.store,
            feature_root=self.feature_root,
            observed_at=observed,
            checkpoint=checkpoint,
        )
        if original.risk is None or original.risk.payload_hash != episode.U_basis_hash:
            raise ValueError("execution_episode_risk_original_mismatch")
        identity = repository.account(episode.portfolio_id).portfolio_identity
        pub = read_publication_in_session(
            session,
            revision=original.revision,
            observed_at=observed,
            channel=identity.channel,
            owner_id=identity.owner_id,
        )
        from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
            ExecutionFillRecord,
        )

        fill_row = session.get(ExecutionFillRecord, result.fill_ref, populate_existing=True)
        fill = check_fill(fill_row.payload_json, fill_row.payload_hash)
        first = repository.first_episode(episode.id)
        with localcontext() as ctx:
            ctx.prec = 50
            u = original.risk.value
            price = Decimal(fill.raw_price)
            expected = (
                number(price - u),
                number(price + 2 * u),
                number(Decimal(fill.quantity) * u),
            )
        if (
            (first.stop_price, first.target_price, first.initial_R) != expected
            or first.entry_at != fill.executed_at
            or first.initial_quantity != fill.quantity
            or first.reference_price != fill.raw_price
            or episode.stop_price != first.stop_price
            or episode.target_price != first.target_price
            or episode.initial_R != first.initial_R
            or episode.U_basis_hash != first.U_basis_hash
        ):
            raise ValueError("execution_episode_initial_risk_mismatch")
        return original, pub

    def _verify_intent_sources(self, session, intent, archive, observed, checkpoint=None):
        repository = ExecutionRepository(session)
        identity = repository.account(intent.portfolio_id).portfolio_identity
        protocol = repository.protocol(identity.protocol_ref)
        scope, catalog = _catalog(protocol, identity)
        revision = ActionRepository(session).get_revision(intent.revision_id)
        if revision is None or revision.payload_hash != intent.revision_hash:
            raise ValueError("execution_intent_revision_original_mismatch")
        pub = read_publication_in_session(
            session,
            revision=revision,
            observed_at=observed,
            channel=identity.channel,
            owner_id=identity.owner_id,
        )
        if (
            pub.publication.payload_hash != intent.publication_receipt_hash
            or pub.admission_hash != intent.admission_hash
            or pub.opportunity.recipe_hash != identity.method_hash
            or intent.identity_hash != identity.identity_hash
        ):
            raise ValueError("execution_intent_publication_changed")
        if intent.source_kind == "condition":
            original = read_evaluation_in_session(
                session,
                plan_id=intent.plan_id,
                revision_id=intent.revision_id,
                evaluation_id=intent.evaluation_ref,
                store=self.store,
                feature_root=self.feature_root,
                observed_at=observed,
                checkpoint=checkpoint,
            )
            if original.evaluation.evaluation_hash != intent.evaluation_hash or (
                original.evaluation.input_ref,
                original.evaluation.input_hash,
            ) != (intent.evaluation_input_ref, intent.evaluation_input_hash):
                raise ValueError("execution_intent_evaluation_changed")
            if original.risk is None:
                episode = repository.episode(intent.episode_ref, intent.episode_hash)
                risk_original, _ = self._episode_originals(
                    session, episode, archive, observed, checkpoint
                )
            else:
                risk_original = original
        else:
            episode = repository.episode(intent.episode_ref, intent.episode_hash)
            original, _ = self._episode_originals(session, episode, archive, observed, checkpoint)
            risk_original = original
            first = repository.first_episode(episode.id)
            if intent.exit_trigger_deadline != episode.exit_trigger_deadline:
                raise ValueError("execution_protection_deadline_changed")
            if intent.source_kind == "quote":
                source = QuoteRepository(archive).read(session, quote_id=intent.source_quote_ref)
                price = Decimal(source.parsed.bid_text)
                if (
                    source.payload_hash != intent.source_quote_hash
                    or source.availability_hash != intent.availability_hash
                    or source.parsed.scope != scope
                    or source.available_at is None
                    or not first.recorded_at
                    <= source.available_at
                    <= min(intent.observed_committed_at, episode.exit_trigger_deadline)
                    or not _fresh_quote(source, protocol, source.available_at)
                    or intent.trigger_input_known_at != source.available_at
                    or not (
                        price <= Decimal(episode.stop_price)
                        if intent.reason == "stop"
                        else price >= Decimal(episode.target_price)
                    )
                ):
                    raise ValueError("execution_protection_quote_mismatch")
            elif (
                intent.deadline_basis != episode.exit_trigger_deadline
                or intent.observed_committed_at < intent.deadline_basis
            ):
                raise ValueError("execution_protection_expiry_mismatch")
        risk = risk_original.risk
        row = session.get(RunRoleOutputRecord, intent.U_basis_ref, populate_existing=True)
        if (
            risk is None
            or row is None
            or row.role != "action_execution_u"
            or row.payload_json != risk.payload_json
            or row.payload_hash != risk.payload_hash
            or (
                intent.U_basis_hash,
                intent.risk_unit_value,
                intent.risk_unit_unit,
                intent.U_measurement_end,
                intent.U_knowledge_cutoff,
                intent.U_known_at,
            )
            != (
                risk.payload_hash,
                number(risk.value),
                risk.unit,
                risk.measurement_end,
                risk.knowledge_cutoff,
                risk.known_at,
            )
        ):
            raise ValueError("execution_intent_risk_original_mismatch")
        target = catalog["targets"][identity.horizon]
        if (
            intent.catalog_hash,
            intent.calculation_manifest_hash,
            intent.target_spec_hash,
            intent.cost_policy_hash,
            intent.size_policy_hash,
        ) != (
            canonical_hash(catalog),
            protocol.calculation_manifest_hash,
            target["hash"],
            catalog["cost_policy"]["hash"],
            catalog["size_policy"]["hash"],
        ):
            raise ValueError("execution_intent_policy_original_mismatch")
        return original, risk_original

    @staticmethod
    def _intent(value, now, key, u_ref, risk):
        identity = value.portfolio.portfolio_identity
        evaluation = value.evaluation
        pub = value.publication
        display = pub.display
        window = execution_window(
            publication_available_at=now,
            trigger_input_known_at=evaluation.input_known_at,
            evaluation_available_at=now,
            admission_valid_at=pub.admission_at,
            channel=identity.channel,
            delay_seconds=identity.delay_seconds,
            display_received_at=display.accepted_at if display else None,
        )
        if window is None:
            raise ValueError("execution_exact_owner_display_required")
        catalog = value.catalog
        target = catalog["targets"][identity.horizon]
        request_hash = canonical_hash(
            [
                identity.identity_hash,
                evaluation.revision.payload_hash,
                evaluation.evaluation.evaluation_hash,
            ]
        )
        return seal(
            ActionExecutionIntentV1,
            body_for(
                ActionExecutionIntentV1,
                schema_version="action-execution-intent.v1",
                id="intent_" + canonical_hash([identity.id, key]),
                portfolio_id=identity.id,
                identity_hash=identity.identity_hash,
                opportunity_id=pub.opportunity.id,
                plan_id=evaluation.revision.plan_id,
                revision_id=evaluation.revision.revision_id,
                revision_hash=evaluation.revision.payload_hash,
                event_key=key,
                source_kind="condition",
                source_identity=evaluation.evaluation.id,
                phase=evaluation.phase,
                action=evaluation.action,
                reason=evaluation.reason
                if evaluation.phase == "entry"
                else "invalidation"
                if evaluation.reason == "invalidation"
                else "condition_exit",
                publication_receipt_ref="action-command:"
                + pub.publication.owner_id
                + ":"
                + pub.publication.request_id,
                publication_receipt_hash=pub.publication.payload_hash,
                U_basis_ref=u_ref,
                U_basis_hash=risk.payload_hash,
                calculation_manifest_ref=ACTION_MONITOR_MANIFEST_REF,
                calculation_manifest_hash=value.protocol.calculation_manifest_hash,
                catalog_ref="research-action-catalog.v1:" + identity.scope_id,
                catalog_hash=canonical_hash(catalog),
                size_policy_ref=catalog["size_policy"]["ref"],
                size_policy_hash=catalog["size_policy"]["hash"],
                cost_policy_ref=catalog["cost_policy"]["ref"],
                cost_policy_hash=catalog["cost_policy"]["hash"],
                target_spec_ref=target["ref"],
                target_spec_hash=target["hash"],
                admission_ref=pub.admission_ref,
                admission_hash=pub.admission_hash,
                evaluation_ref=evaluation.evaluation.id,
                evaluation_hash=evaluation.evaluation.evaluation_hash,
                evaluation_input_ref=evaluation.evaluation.input_ref,
                evaluation_input_hash=evaluation.evaluation.input_hash,
                episode_ref=episode_ref(value.episode) if value.episode else None,
                episode_hash=value.episode.payload_hash if value.episode else None,
                display_receipt_ref="action-command:" + display.owner_id + ":" + display.request_id
                if display
                else None,
                display_receipt_hash=display.payload_hash if display else None,
                risk_unit_value=number(risk.value),
                risk_unit_unit=risk.unit,
                U_measurement_end=risk.measurement_end,
                U_knowledge_cutoff=risk.knowledge_cutoff,
                U_known_at=risk.known_at,
                channel=identity.channel,
                owner_id=identity.owner_id,
                publication_available_at=now,
                trigger_input_known_at=evaluation.input_known_at,
                admission_valid_at=pub.admission_at,
                observed_committed_at=now,
                base_action_at=window.base_at,
                eligible_at=window.eligible_at,
                quote_deadline=window.quote_deadline,
                created_at=now,
                evaluation_available_at=now,
                display_received_at=display.accepted_at if display else None,
                entry_valid_until=min(evaluation.revision.valid_until, pub.opportunity.time_end)
                if evaluation.phase == "entry"
                else None,
                exit_trigger_deadline=value.episode.exit_trigger_deadline
                if value.episode
                else None,
                delay_seconds=identity.delay_seconds,
                request_hash=request_hash,
            ),
        )

    def execute_intent(self, intent_id: str, *, checkpoint=None):
        if self.perpetual_execution is not None:
            with self.database.session() as session:
                row = session.get(ExecutionIntentRecord, intent_id, populate_existing=True)
                manual = (
                    row is not None
                    and json.loads(row.payload_json).get("schema_version")
                    == "perpetual-manual-execution-intent.v1"
                )
            if manual:
                if not self.enabled:
                    raise ValueError("action_execution_disabled")
                return self.perpetual_execution.execute_intent(intent_id, checkpoint=checkpoint)
        if not self.enabled:
            raise ValueError("action_execution_disabled")
        with self.database.session() as session:
            old = ExecutionRepository(session).result(intent_id)
            if old:
                return old
        now = self._now()
        archive = _ArchiveReadSet(self.store.archive)

        def read(session):
            repository = ExecutionRepository(session)
            intent = repository.intent(intent_id)
            account = repository.account(intent.portfolio_id)
            identity = account.portfolio_identity
            protocol = repository.protocol(identity.protocol_ref)
            scope, catalog = _catalog(protocol, identity)
            if checkpoint:
                checkpoint()
            revision = ActionRepository(session).get_revision(intent.revision_id)
            if revision is None or revision.payload_hash != intent.revision_hash:
                raise ValueError("execution_revision_original_mismatch")
            publication = read_publication_in_session(
                session,
                revision=revision,
                observed_at=now,
                channel=identity.channel,
                owner_id=identity.owner_id,
            )
            if (
                publication.publication.payload_hash != intent.publication_receipt_hash
                or publication.admission_hash != intent.admission_hash
                or publication.opportunity.recipe_hash != identity.method_hash
            ):
                raise ValueError("execution_intent_publication_changed")
            episode = repository.open_episode(intent.portfolio_id)
            selection = QuoteRepository(archive).first_eligible(
                session,
                scope=scope,
                eligible_at=intent.eligible_at,
                as_of=now,
                clock_policy=quote_clock_policy(protocol),
            )
            evidence, risk_evidence = self._verify_intent_sources(
                session, intent, archive, now, checkpoint
            )
            return (
                intent,
                account,
                protocol,
                catalog,
                publication,
                evidence,
                risk_evidence,
                episode,
                selection,
            )

        prepared = prepare_originals(self.database, read)
        intent, account, protocol, catalog, pub, evidence, risk_evidence, episode, selection = (
            prepared.value
        )
        if now < intent.eligible_at or selection.selected is None and now <= intent.quote_deadline:
            return None
        with self.database.session() as session:
            begin_immediate(session)
            repository = ExecutionRepository(session)
            old = repository.result(intent_id)
            if old:
                return old
            prepared.verify(session)
            archive.verify()
            if evidence:
                evidence.verify_bytes(self.store)
            if risk_evidence is not evidence:
                risk_evidence.verify_bytes(self.store)
            writer = self._now()
            current = repository.account(intent.portfolio_id)
            if current.payload_hash != account.payload_hash:
                raise ValueError("execution_account_changed_after_read")
            head = ActionRepository(session).get_head(intent.plan_id)
            rejection = None
            if intent.phase == "entry" and (
                head is None
                or head.current_revision_id != intent.revision_id
                or head.state != "triggered"
                or head.monitor_state != "active"
            ):
                rejection = "entry_paused_replaced_or_ended"
            if intent.phase == "entry" and (episode is not None or account_for(current).quantity):
                rejection = "existing_position_forbids_new_entry"
            if intent.phase != "entry" and (
                episode is None
                or intent.episode_ref is None
                or episode.id != intent.episode_ref.rsplit(":v", 1)[0]
            ):
                rejection = "protection_episode_no_longer_open"
            quote = selection.selected
            if quote and not rejection:
                try:
                    _validate_quote_for_intent(quote, protocol, intent, writer)
                except ValueError as error:
                    rejection = str(error)
            if rejection or quote is None:
                affected_episode = (
                    None if rejection == "protection_episode_no_longer_open" else episode
                )
                return self._finish(
                    session,
                    intent,
                    current,
                    protocol,
                    pub,
                    affected_episode,
                    None,
                    None,
                    selection,
                    writer,
                    "rejected" if rejection else "no_fill",
                    rejection or "quote_window_exhausted",
                )
            state = account_for(current)
            market = Quote(
                current.portfolio_identity.scope_id,
                Decimal(quote.parsed.bid_text),
                Decimal(quote.parsed.ask_text),
            )
            if intent.phase == "entry":
                with localcontext() as context:
                    context.prec = 50
                    stop = market.ask - Decimal(intent.risk_unit_value)
                    equity = marked_equity(state, market)
                if stop <= 0:
                    return self._finish(
                        session,
                        intent,
                        current,
                        protocol,
                        pub,
                        episode,
                        None,
                        None,
                        selection,
                        writer,
                        "rejected",
                        "nonpositive_stop",
                    )
                quantity = size_entry(state, market, equity=equity, stop_price=stop)
                if not quantity:
                    return self._finish(
                        session,
                        intent,
                        current,
                        protocol,
                        pub,
                        episode,
                        None,
                        None,
                        selection,
                        writer,
                        "no_fill",
                        "entry_risk_cash_cap_zero",
                    )
                after, fill = buy(state, market, quantity=quantity, equity=equity, stop_price=stop)
            else:
                if episode is None or not state.quantity:
                    return self._finish(
                        session,
                        intent,
                        current,
                        protocol,
                        pub,
                        episode,
                        None,
                        None,
                        selection,
                        writer,
                        "rejected",
                        "position_missing",
                    )
                quantity = reduce_quantity(state) if intent.phase == "reduce" else state.quantity
                if not quantity:
                    return self._finish(
                        session,
                        intent,
                        current,
                        protocol,
                        pub,
                        episode,
                        None,
                        None,
                        selection,
                        writer,
                        "no_fill",
                        "quantity_below_step",
                    )
                after, fill = sell(state, market, quantity=quantity)
            return self._finish(
                session,
                intent,
                current,
                protocol,
                pub,
                episode,
                after,
                fill,
                selection,
                writer,
                "filled",
                "paper_fill",
            )

    def _finish(
        self,
        session,
        intent,
        current,
        protocol,
        pub,
        old_episode,
        after,
        arithmetic,
        selection,
        now,
        status,
        reason,
    ):
        repository = ExecutionRepository(session)
        identity = current.portfolio_identity
        quote = selection.selected
        fill = None
        episode = old_episode
        new_checkpoint = None
        if arithmetic:
            assert quote is not None and after is not None
            episode_id = (
                old_episode.id
                if old_episode
                else "episode_" + canonical_hash([identity.id, intent.id])
            )
            fill_type = (
                ActionFillV2 if quote_clock_policy(protocol) == "bracket_v1" else ActionFillV1
            )
            clock_fields = {}
            if fill_type is ActionFillV2:
                clock = quote.parsed.clock_receipt
                if clock is None:
                    raise ValueError("execution_fill_clock_missing")
                lower, upper = quote.parsed.local_interval(clock_policy="bracket_v1")
                clock_fields = {
                    "clock_ref": clock["id"],
                    "clock_hash": clock["payload_hash"],
                    "observed_local_lower": lower,
                    "observed_local_upper": upper,
                }
            fill = seal(
                fill_type,
                body_for(
                    fill_type,
                    schema_version="action-fill.v2"
                    if fill_type is ActionFillV2
                    else "action-fill.v1",
                    **clock_fields,
                    id="fill_" + canonical_hash([intent.id]),
                    intent_ref="intent:" + intent.id,
                    intent_hash=intent.payload_hash,
                    portfolio_id=identity.id,
                    episode_id=episode_id,
                    side=arithmetic.side,
                    quantity=number(arithmetic.quantity),
                    base_unit=identity.scope_id.split(":")[3].split("-")[0],
                    quote_id=quote.quote_id,
                    quote_hash=quote.payload_hash,
                    availability_hash=quote.availability_hash,
                    capture_ref=quote.parsed.capture.body_ref,
                    capture_hash=quote.parsed.capture.body_hash,
                    provider_observed_at=quote.parsed.observed_at,
                    received_at=quote.parsed.capture.completed_at,
                    available_at=quote.available_at,
                    executed_at=now,
                    bid=number(Decimal(quote.parsed.bid_text)),
                    ask=number(Decimal(quote.parsed.ask_text)),
                    raw_price=number(arithmetic.raw_price),
                    execution_price=number(arithmetic.execution_price),
                    fee=number(arithmetic.fee),
                    slippage=number(arithmetic.slippage),
                    cash_delta=number(arithmetic.cash_delta),
                    gross=number(arithmetic.gross) if arithmetic.gross is not None else None,
                    cost=number(arithmetic.cost) if arithmetic.cost is not None else None,
                    net=number(arithmetic.net) if arithmetic.net is not None else None,
                    cost_policy_ref=intent.cost_policy_ref,
                    cost_policy_hash=intent.cost_policy_hash,
                    account_fee_verified=False,
                    execution_kind="paper",
                ),
            )
            if old_episode:
                ep = old_episode.model_dump(mode="json")
                with localcontext() as context:
                    context.prec = 50
                    totals = {
                        "realized_" + name: number(
                            Decimal(getattr(old_episode, "realized_" + name))
                            + getattr(arithmetic, name)
                        )
                        for name in ("gross", "cost", "net")
                    }
                ep.update(
                    version=old_episode.version + 1,
                    previous_ref=episode_ref(old_episode),
                    previous_hash=old_episode.payload_hash,
                    remaining_quantity=number(after.quantity),
                    status="closed" if not after.quantity else "partially_closed",
                    last_intent_ref="intent:" + intent.id,
                    last_intent_hash=intent.payload_hash,
                    checkpoint_refs=[{"ref": current.id, "hash": current.payload_hash}],
                    **totals,
                    recorded_at=now,
                )
            else:
                with localcontext() as context:
                    context.prec = 50
                    u = Decimal(intent.risk_unit_value)
                    stop = arithmetic.raw_price - u
                    target = arithmetic.raw_price + 2 * u
                    initial_R = arithmetic.quantity * u
                deadline = exit_trigger_deadline(
                    revision_valid_until=intent.entry_valid_until,
                    opportunity_end=pub.opportunity.time_end,
                    entry_at=now,
                    max_hold_seconds=min(
                        {"30m": 1800, "24h": 86400, "72h": 259200}[identity.horizon], 86400
                    ),
                )
                ep = body_for(
                    ActionExecutionEpisodeV1,
                    schema_version="action-execution-episode.v1",
                    id=episode_id,
                    portfolio_id=identity.id,
                    version=1,
                    opening_kind="buy_fill",
                    opening_checkpoint_ref=current.id,
                    opening_checkpoint_hash=current.payload_hash,
                    opportunity_id=intent.opportunity_id,
                    plan_id=intent.plan_id,
                    revision_id=intent.revision_id,
                    revision_hash=intent.revision_hash,
                    entry_intent_ref="intent:" + intent.id,
                    entry_intent_hash=intent.payload_hash,
                    entry_fill_ref=fill.id,
                    entry_fill_hash=fill.payload_hash,
                    entry_at=now,
                    reference_price=fill.raw_price,
                    initial_quantity=fill.quantity,
                    remaining_quantity=fill.quantity,
                    U_basis_ref=intent.U_basis_ref,
                    U_basis_hash=intent.U_basis_hash,
                    risk_unit_value=intent.risk_unit_value,
                    initial_R=number(initial_R),
                    stop_price=number(stop),
                    target_price=number(target),
                    original_DSL_revision_ref=intent.revision_id,
                    original_DSL_revision_hash=intent.revision_hash,
                    exit_trigger_deadline=deadline,
                    status="open",
                    last_intent_ref="intent:" + intent.id,
                    last_intent_hash=intent.payload_hash,
                    checkpoint_refs=[{"ref": current.id, "hash": current.payload_hash}],
                    realized_gross="0",
                    realized_cost="0",
                    realized_net="0",
                    price_coverage="0",
                    exclusions=["sampled_quotes_not_continuous"],
                    recorded_at=now,
                )
            episode = seal(ActionExecutionEpisodeV1, ep)
            new_checkpoint = seal(
                ActionPaperCheckpointV1,
                body_for(
                    ActionPaperCheckpointV1,
                    schema_version="action-paper-checkpoint.v1",
                    id="checkpoint_" + canonical_hash([identity.id, current.version + 1]),
                    portfolio_identity=identity.model_dump(mode="json"),
                    portfolio_id=identity.id,
                    identity_hash=identity.identity_hash,
                    version=current.version + 1,
                    previous_ref=current.id,
                    previous_hash=current.payload_hash,
                    execution_key=intent.id,
                    request_hash=intent.request_hash,
                    created_at=now,
                    account_before_hash=canonical_hash(
                        current.account_after.model_dump(mode="json")
                    ),
                    account_after=account_values(after),
                    event_kind="fill",
                    opening_original_refs=[],
                    intent_ref="intent:" + intent.id,
                    intent_hash=intent.payload_hash,
                    fill_ref=fill.id,
                    fill_hash=fill.payload_hash,
                    episode_ref=episode_ref(episode),
                    episode_hash=episode.payload_hash,
                    disposition="filled",
                    exclusions=[],
                ),
            )
            repository.append_fill(fill)
            repository.append_episode(
                episode,
                next_due_at=None
                if episode.status == "closed"
                else min(now + timedelta(seconds=10), episode.exit_trigger_deadline),
            )
            repository.append_checkpoint(new_checkpoint)
        elif intent.phase == "exit" and old_episode is not None:
            # Frozen quote window failure is a terminal unknown result, not a
            # later price retry or a fabricated sale. Inventory/cash stay intact.
            body = old_episode.model_dump(mode="json")
            body.update(
                version=old_episode.version + 1,
                previous_ref=episode_ref(old_episode),
                previous_hash=old_episode.payload_hash,
                status="unpriced",
                last_intent_ref="intent:" + intent.id,
                last_intent_hash=intent.payload_hash,
                checkpoint_refs=[{"ref": current.id, "hash": current.payload_hash}],
                exclusions=list(
                    dict.fromkeys([*old_episode.exclusions, "exit_unfilled:" + reason])
                ),
                recorded_at=now,
            )
            episode = seal(ActionExecutionEpisodeV1, body)
            repository.append_episode(episode, next_due_at=None)
        prior = repository.latest_outcome(
            intent.plan_id, intent.revision_id, identity.identity_hash
        )
        version = prior.version + 1 if prior else 1
        closed = episode is not None and episode.status == "closed" and fill is not None
        with localcontext() as context:
            context.prec = 50
            r = (
                number(Decimal(episode.realized_net) / Decimal(episode.initial_R))
                if closed and episode is not None
                else None
            )
            fills = repository.episode_fills(episode.id, through=now) if closed and episode else ()
            fee = (
                number(sum((Decimal(item.fee) for item in fills), Decimal(0)))
                if closed
                else fill.fee
                if fill
                else None
            )
            slippage = (
                number(sum((Decimal(item.slippage) for item in fills), Decimal(0)))
                if closed
                else fill.slippage
                if fill
                else None
            )
        outcome = seal(
            ActionOutcomeV2,
            body_for(
                ActionOutcomeV2,
                schema_version="action-outcome.v2",
                id="outcome_"
                + canonical_hash([identity.id, intent.plan_id, intent.revision_id, version]),
                opportunity_id=intent.opportunity_id,
                plan_id=intent.plan_id,
                revision_id=intent.revision_id,
                mode="paper",
                status="settled" if closed else "pending" if status == "filled" else "unscorable",
                protocol_ref=protocol.id,
                protocol_hash=identity.identity_hash,
                registered_protocol_hash=protocol.payload_hash,
                visible_at=intent.display_received_at,
                trigger_at=intent.trigger_input_known_at,
                entry_at=episode.entry_at if episode else None,
                exit_at=now if closed else None,
                price_basis="bid_ask",
                entry=episode.reference_price if episode else None,
                exit=fill.raw_price if closed and fill else None,
                gross_pnl=episode.realized_gross if closed and episode else None,
                cost=episode.realized_cost if closed and episode else None,
                net_pnl=episode.realized_net if closed and episode else None,
                coverage="0",
                price_refs=[{"ref": quote.quote_id, "hash": quote.payload_hash}] if quote else [],
                exclusions=["diagnostic_only", "sampled_quotes_not_continuous"]
                + ([] if status == "filled" else [reason]),
                settled_at=now if closed else None,
                version=version,
                due_at=episode.exit_trigger_deadline if episode else intent.quote_deadline,
                portfolio_id=identity.id,
                identity_hash=identity.identity_hash,
                scope_id=identity.scope_id,
                horizon=identity.horizon,
                scenario=identity.scenario,
                method_ref=identity.method_ref,
                method_hash=identity.method_hash,
                channel=identity.channel,
                delay_seconds=identity.delay_seconds,
                owner_id=identity.owner_id,
                execution_ref="intent:" + intent.id,
                execution_hash=intent.payload_hash,
                episode_ref=episode_ref(episode) if episode else None,
                episode_hash=episode.payload_hash if episode else None,
                previous_ref=prior.id if prior else None,
                previous_hash=prior.payload_hash if prior else None,
                episode_version=episode.version if episode else None,
                execution_status=status,
                quantity=episode.initial_quantity if episode else None,
                remaining_quantity=episode.remaining_quantity if episode else None,
                fee=fee,
                slippage=slippage,
                R=r,
                financial_qualified=False,
                qualification_reasons=[
                    "A1_diagnostic_not_A28",
                    "sampled_quotes_not_continuous",
                    "fees_assumed_not_account_verified",
                ],
            ),
        )
        repository.append_outcome(outcome)
        receipt = seal(
            ActionExecutionReceiptV1,
            body_for(
                ActionExecutionReceiptV1,
                schema_version="action-execution-receipt.v1",
                id="execution_" + canonical_hash([intent.id]),
                intent_ref="intent:" + intent.id,
                intent_hash=intent.payload_hash,
                portfolio_id=identity.id,
                request_hash=intent.request_hash,
                status=status,
                reason=reason,
                first_quote_ref=quote.quote_id if quote else None,
                first_quote_hash=quote.payload_hash if quote else None,
                fill_ref=fill.id if fill else None,
                fill_hash=fill.payload_hash if fill else None,
                checkpoint_ref=new_checkpoint.id if new_checkpoint else None,
                checkpoint_hash=new_checkpoint.payload_hash if new_checkpoint else None,
                episode_ref=episode_ref(episode) if episode else None,
                episode_hash=episode.payload_hash if episode else None,
                outcome_ref=outcome.id,
                outcome_hash=outcome.payload_hash,
                quote_exclusions=[
                    item.reason + ":" + item.quote_id for item in selection.exclusions
                ],
                decided_at=now,
            ),
        )
        return repository.append_result(receipt)

    def observe_episode(self, portfolio_id: str, *, checkpoint=None):
        if self.perpetual_execution is not None:
            with self.database.session() as session:
                row = session.get(ExecutionEpisodeHeadRecord, portfolio_id, populate_existing=True)
                manual = row is not None and row.scope_id.startswith("perp-book:")
            if manual:
                if not self.enabled:
                    raise ValueError("action_execution_disabled")
                return self.perpetual_execution.observe_episode(portfolio_id, checkpoint=checkpoint)
        """Observe earliest unconsumed protective quote or due exit; no fill here."""
        if not self.enabled:
            raise ValueError("action_execution_disabled")
        observed = self._now()
        archive = _ArchiveReadSet(self.store.archive)

        def read(session):
            repository = ExecutionRepository(session)
            episode = repository.open_episode(portfolio_id)
            if episode is None:
                return None
            key = "episode-exit:" + episode.id
            existing = repository.intent_by_key(portfolio_id, key)
            if existing:
                return existing
            account = repository.account(portfolio_id)
            identity = account.portfolio_identity
            protocol = repository.protocol(identity.protocol_ref)
            scope, _ = _catalog(protocol, identity)
            original, publication = self._episode_originals(
                session, episode, archive, observed, checkpoint
            )
            initial = repository.first_episode(episode.id)
            head = session.get(ExecutionEpisodeHeadRecord, portfolio_id, populate_existing=True)
            query = (
                select(QuoteRecord)
                .join(AvailabilityRecord, AvailabilityRecord.quote_id == QuoteRecord.quote_id)
                .where(
                    QuoteRecord.scope_id == scope.scope_id,
                    AvailabilityRecord.available_at >= initial.recorded_at,
                    AvailabilityRecord.available_at <= observed,
                )
            )
            if head.quote_cursor_available_at is not None:
                query = query.where(
                    tuple_(
                        AvailabilityRecord.available_at,
                        QuoteRecord.received_at,
                        QuoteRecord.capture_id,
                    )
                    > (
                        head.quote_cursor_available_at,
                        head.quote_cursor_received_at,
                        head.quote_cursor_capture_id,
                    )
                )
            rows = session.scalars(
                query.order_by(
                    AvailabilityRecord.available_at, QuoteRecord.received_at, QuoteRecord.capture_id
                ).limit(129)
            ).all()
            quotes = []
            chosen = None
            for row in rows[:128]:
                if checkpoint:
                    checkpoint()
                quote = QuoteRepository(archive).read(session, quote_id=row.quote_id)
                quotes.append(quote)
                if quote.available_at is None or not _fresh_quote(
                    quote, protocol, quote.available_at
                ):
                    continue
                bid = Decimal(quote.parsed.bid_text)
                reason = (
                    "stop"
                    if bid <= Decimal(episode.stop_price)
                    else "target"
                    if bid >= Decimal(episode.target_price)
                    else None
                )
                if reason and quote.available_at <= episode.exit_trigger_deadline:
                    chosen = (reason, quote)
                    break
            cursor = (
                None
                if not quotes
                else (
                    quotes[-1].available_at,
                    quotes[-1].parsed.capture.completed_at,
                    quotes[-1].parsed.capture.capture_id,
                )
            )
            expiry = (
                chosen is None and len(rows) <= 128 and observed >= episode.exit_trigger_deadline
            )
            return (
                episode,
                identity,
                protocol,
                original,
                publication,
                chosen,
                expiry,
                cursor,
                len(rows) > 128,
            )

        prepared = prepare_originals(self.database, read)
        value = prepared.value
        if value is None or isinstance(value, ActionExecutionIntentV1):
            return value
        episode, identity, protocol, original, pub, chosen, expiry, cursor, backlog = value
        with self.database.session() as session:
            begin_immediate(session)
            repository = ExecutionRepository(session)
            old = repository.intent_by_key(portfolio_id, "episode-exit:" + episode.id)
            if old:
                return old
            prepared.verify(session)
            archive.verify()
            original.verify_bytes(self.store)
            now = self._now()
            head = session.get(ExecutionEpisodeHeadRecord, portfolio_id, populate_existing=True)
            if head is None or head.payload_hash != episode.payload_hash:
                raise ValueError("execution_episode_changed_after_read")
            if chosen is None and not expiry:
                updates = {
                    "next_due_at": now
                    if backlog
                    else min(now + timedelta(seconds=10), episode.exit_trigger_deadline)
                }
                if cursor:
                    updates.update(
                        quote_cursor_available_at=cursor[0],
                        quote_cursor_received_at=cursor[1],
                        quote_cursor_capture_id=cursor[2],
                    )
                session.execute(
                    update(ExecutionEpisodeHeadRecord)
                    .where(
                        ExecutionEpisodeHeadRecord.portfolio_id == portfolio_id,
                        ExecutionEpisodeHeadRecord.payload_hash == episode.payload_hash,
                    )
                    .values(**updates)
                )
                return None
            reason, quote = chosen if chosen else ("expiry", None)
            known = quote.available_at if quote else episode.exit_trigger_deadline
            base = max(
                now,
                known,
                pub.admission_at,
                pub.display.accepted_at if pub.display else pub.admission_at,
            )
            key = "episode-exit:" + episode.id
            _, catalog = _catalog(protocol, identity)
            target = catalog["targets"][identity.horizon]
            risk = original.risk
            if risk is None:
                raise ValueError("execution_authenticated_u_required")
            display = pub.display
            payload = body_for(
                ActionExecutionIntentV1,
                schema_version="action-execution-intent.v1",
                portfolio_id=portfolio_id,
                identity_hash=identity.identity_hash,
                opportunity_id=pub.opportunity.id,
                plan_id=original.revision.plan_id,
                revision_id=original.revision.revision_id,
                revision_hash=original.revision.payload_hash,
                publication_receipt_ref="action-command:"
                + pub.publication.owner_id
                + ":"
                + pub.publication.request_id,
                publication_receipt_hash=pub.publication.payload_hash,
                U_basis_ref=episode.U_basis_ref,
                U_basis_hash=episode.U_basis_hash,
                calculation_manifest_ref=ACTION_MONITOR_MANIFEST_REF,
                calculation_manifest_hash=protocol.calculation_manifest_hash,
                catalog_ref="research-action-catalog.v1:" + identity.scope_id,
                catalog_hash=canonical_hash(catalog),
                size_policy_ref=catalog["size_policy"]["ref"],
                size_policy_hash=catalog["size_policy"]["hash"],
                cost_policy_ref=catalog["cost_policy"]["ref"],
                cost_policy_hash=catalog["cost_policy"]["hash"],
                target_spec_ref=target["ref"],
                target_spec_hash=target["hash"],
                admission_ref=pub.admission_ref,
                admission_hash=pub.admission_hash,
                display_receipt_ref="action-command:" + display.owner_id + ":" + display.request_id
                if display
                else None,
                display_receipt_hash=display.payload_hash if display else None,
                risk_unit_value=number(risk.value),
                risk_unit_unit=risk.unit,
                U_measurement_end=risk.measurement_end,
                U_knowledge_cutoff=risk.knowledge_cutoff,
                U_known_at=risk.known_at,
                channel=identity.channel,
                owner_id=identity.owner_id,
                publication_available_at=now,
                admission_valid_at=pub.admission_at,
                display_received_at=display.accepted_at if display else None,
                delay_seconds=identity.delay_seconds,
            )
            payload.update(
                id="intent_" + canonical_hash([portfolio_id, key]),
                event_key=key,
                source_kind="quote" if quote else "deadline",
                source_identity=quote.quote_id if quote else episode_ref(episode),
                phase="exit",
                action="exit_long",
                reason=reason,
                evaluation_ref=None,
                evaluation_hash=None,
                evaluation_input_ref=None,
                evaluation_input_hash=None,
                evaluation_available_at=None,
                source_quote_ref=quote.quote_id if quote else None,
                source_quote_hash=quote.payload_hash if quote else None,
                availability_hash=quote.availability_hash if quote else None,
                deadline_basis=episode.exit_trigger_deadline if not quote else None,
                episode_ref=episode_ref(episode),
                episode_hash=episode.payload_hash,
                trigger_input_known_at=known,
                observed_committed_at=now,
                base_action_at=base,
                eligible_at=base + timedelta(seconds=identity.delay_seconds),
                quote_deadline=base + timedelta(seconds=identity.delay_seconds + 60),
                created_at=now,
                entry_valid_until=None,
                exit_trigger_deadline=episode.exit_trigger_deadline,
                request_hash=canonical_hash(
                    [
                        identity.identity_hash,
                        key,
                        episode.payload_hash,
                        quote.payload_hash if quote else known.isoformat(),
                    ]
                ),
            )
            intent = seal(ActionExecutionIntentV1, payload)
            repository.append_intent(intent)
            return intent

    def process_due(self, *, max_intents: int = 8, checkpoint=None) -> dict[str, Any]:
        """Existing tick calls this bounded consumer; never creates another worker."""
        if not self.enabled:
            return {"processed": 0, "errors": [], "disabled": True}
        if type(max_intents) is not int or not 1 <= max_intents <= 12:
            raise ValueError("execution_due_budget_invalid")
        if self.perpetual_execution is not None:
            from .perpetual_quote_sampling import PerpetualQuoteSamplingAdapter
            PerpetualQuoteSamplingAdapter(self.perpetual_execution).process_due(checkpoint=checkpoint)
        from packages.kernel.decision_hub_kernel.application.action_execution_observer import (
            ExecutionObservationService,
        )

        discovered = ExecutionObservationService(self).process_due(checkpoint=checkpoint)
        with self.database.session() as session:
            portfolios = session.scalars(
                select(ExecutionEpisodeHeadRecord.portfolio_id)
                .where(
                    ExecutionEpisodeHeadRecord.active.is_(True),
                    ExecutionEpisodeHeadRecord.next_due_at <= self._now(),
                )
                .order_by(
                    ExecutionEpisodeHeadRecord.next_due_at, ExecutionEpisodeHeadRecord.portfolio_id
                )
                .limit(4)
            ).all()
        errors = list(discovered["errors"])
        for portfolio in portfolios:
            if checkpoint:
                checkpoint()
            try:
                self.observe_episode(portfolio, checkpoint=checkpoint)
            except ValueError as error:
                errors.append({"portfolio_id": portfolio, "reason": str(error)})
        with self.database.session() as session:
            pending = session.scalars(
                select(ExecutionIntentRecord.intent_id)
                .outerjoin(
                    ExecutionReceiptRecord,
                    ExecutionReceiptRecord.intent_id == ExecutionIntentRecord.intent_id,
                )
                .where(
                    ExecutionReceiptRecord.intent_id.is_(None),
                    ExecutionIntentRecord.eligible_at <= self._now(),
                )
                .order_by(ExecutionIntentRecord.eligible_at, ExecutionIntentRecord.intent_id)
                .limit(max_intents)
            ).all()
        completed = 0
        for identity in pending:
            if checkpoint:
                checkpoint()
            try:
                completed += self.execute_intent(identity, checkpoint=checkpoint) is not None
            except ValueError as error:
                errors.append({"intent_id": identity, "reason": str(error)})
        # Financial protection and accepted intents run before optional source reconciliation.
        funding_reconciled = 0
        if self.perpetual_execution is not None and getattr(self.perpetual_execution, "funding_history", None) is not None:
            from httpx import HTTPError

            with self.database.session() as session:
                closed = session.scalars(
                    select(ExecutionEpisodeHeadRecord.portfolio_id)
                    .where(
                        ExecutionEpisodeHeadRecord.active.is_(False),
                        ExecutionEpisodeHeadRecord.remaining_nonzero.is_(False),
                        ExecutionEpisodeHeadRecord.next_due_at <= self._now(),
                        ExecutionEpisodeHeadRecord.scope_id.startswith("perp-book:okx:"),
                    )
                    .order_by(
                        ExecutionEpisodeHeadRecord.next_due_at,
                        ExecutionEpisodeHeadRecord.portfolio_id,
                    )
                    .limit(4)
                ).all()
            for portfolio in closed:
                if checkpoint:
                    checkpoint()
                try:
                    result = self.perpetual_execution.reconcile_closed_episode(
                        portfolio, checkpoint=checkpoint
                    )
                    funding_reconciled += bool(
                        result and result.get("status") == "reconciled"
                    )
                except (ValueError, HTTPError, OSError, TimeoutError) as error:
                    reason = str(error) if isinstance(error, ValueError) else ""
                    if not (reason.startswith("perp_") and len(reason) <= 128 and reason.replace("_", "").isalnum()):
                        reason = "perp_funding_source_temporarily_unavailable"
                    errors.append({"portfolio_id": portfolio, "reason": reason})
                    # Finish bounded local retry metadata even after the source tick expired.
                    try:
                        self.perpetual_execution.defer_closed_episode(
                            portfolio, reason, checkpoint=None
                        )
                    except ValueError:
                        errors.append({"portfolio_id": portfolio, "reason": "perp_funding_retry_state_unavailable"})
        return {
            "processed": completed,
            "observed": discovered["observed"],
            "funding_reconciled": funding_reconciled,
            "errors": errors,
            "disabled": False,
        }

    def read_observation(self, observation_id: str):
        from packages.kernel.decision_hub_kernel.application.action_execution_observer import (
            ExecutionObservationService,
        )

        return ExecutionObservationService(self).read(observation_id)

    def opportunities(self, portfolio_id: str, *, after: str | None = None, limit: int = 50):
        from packages.kernel.decision_hub_kernel.application.action_execution_observer import (
            ExecutionObservationService,
        )

        return ExecutionObservationService(self).opportunities(
            portfolio_id, after=after, limit=limit
        )

    def observations(
        self, portfolio_id: str, opportunity_id: str, *, after: str | None = None, limit: int = 50
    ):
        from packages.kernel.decision_hub_kernel.application.action_execution_observer import (
            ExecutionObservationService,
        )

        return ExecutionObservationService(self).observations(
            portfolio_id, opportunity_id, after=after, limit=limit
        )

    def read_outcome(self, outcome_id: str):
        return self.prepare_outcome(outcome_id).value

    def prepare_outcome(self, outcome_id: str):
        """W7 identity-only read: immutable execution DAG plus genuine raw quotes.

        Returned available_at is this read's actual observation, never historical
        visibility inferred from a model timestamp. Reflection must freeze its own
        read receipt rather than treat the DTO as a transferable permission token.
        """
        from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
            ExecutionFillRecord,
        )
        from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
            check,
        )
        from packages.kernel.decision_hub_kernel.persistence.action_records import (
            ActionOutcomeRecord,
        )

        observed = self._now()
        archive = _ArchiveReadSet(self.store.archive)

        def read(session):
            repository = ExecutionRepository(session)
            row = session.get(ActionOutcomeRecord, outcome_id, populate_existing=True)
            if row is None:
                raise ValueError("execution_outcome_missing")
            outcome = check(ActionOutcomeV2, row.payload_json, row.payload_hash)
            if outcome.id != outcome_id or not outcome.execution_ref.startswith("intent:"):
                raise ValueError("execution_outcome_intent_namespace_required")
            intent = repository.intent(outcome.execution_ref.removeprefix("intent:"))
            result = repository.result(intent.id)
            if (
                result is None
                or result.outcome_ref != outcome_id
                or result.outcome_hash != outcome.payload_hash
                or result.decided_at > observed
                or outcome.settled_at is not None
                and outcome.settled_at > observed
            ):
                raise ValueError("execution_outcome_result_mismatch")
            identity = repository.account(intent.portfolio_id).portfolio_identity
            protocol = repository.protocol(identity.protocol_ref)
            scope, _ = _catalog(protocol, identity)
            revision = ActionRepository(session).get_revision(intent.revision_id)
            if revision is None or revision.payload_hash != intent.revision_hash:
                raise ValueError("execution_outcome_revision_mismatch")
            pub = read_publication_in_session(
                session,
                revision=revision,
                observed_at=observed,
                channel=identity.channel,
                owner_id=identity.owner_id,
            )
            if (
                pub.publication.payload_hash != intent.publication_receipt_hash
                or pub.admission_hash != intent.admission_hash
                or pub.opportunity.recipe_hash != identity.method_hash
            ):
                raise ValueError("execution_outcome_publication_mismatch")
            evaluation, risk_evaluation = self._verify_intent_sources(
                session, intent, archive, observed
            )
            if result.fill_ref:
                fill_row = session.get(ExecutionFillRecord, result.fill_ref, populate_existing=True)
                fill = check_fill(fill_row.payload_json, fill_row.payload_hash)
                quote = QuoteRepository(archive).read(session, quote_id=fill.quote_id)
                if (
                    quote.payload_hash != fill.quote_hash
                    or quote.availability_hash != fill.availability_hash
                    or quote.parsed.scope != scope
                    or quote.parsed.capture.body_hash != fill.capture_hash
                ):
                    raise ValueError("execution_outcome_quote_mismatch")
                _validate_quote_for_intent(quote, protocol, intent, fill.executed_at)
                _verify_fill_quote(fill, quote, protocol)
                current = repository.checkpoint(result.checkpoint_ref, result.checkpoint_hash)
                previous = repository.checkpoint(current.previous_ref, current.previous_hash)
                state = account_for(previous)
                market = Quote(
                    scope.scope_id, Decimal(quote.parsed.bid_text), Decimal(quote.parsed.ask_text)
                )
                if fill.side == "buy":
                    stop = Decimal(fill.raw_price) - Decimal(intent.risk_unit_value)
                    after, actual = buy(
                        state,
                        market,
                        quantity=Decimal(fill.quantity),
                        equity=marked_equity(state, market),
                        stop_price=stop,
                    )
                else:
                    after, actual = sell(state, market, quantity=Decimal(fill.quantity))
                if account_values(after) != current.account_after.model_dump() or any(
                    getattr(fill, name)
                    != (
                        number(getattr(actual, name)) if getattr(actual, name) is not None else None
                    )
                    for name in (
                        "fee",
                        "slippage",
                        "cash_delta",
                        "gross",
                        "cost",
                        "net",
                        "execution_price",
                    )
                ):
                    raise ValueError("execution_outcome_accounting_mismatch")
            if outcome.status == "settled":
                episode = repository.episode(outcome.episode_ref, outcome.episode_hash)
                fills = repository.episode_fills(episode.id, through=result.decided_at)
                # Each fill is bound to its own exact immutable result. This is
                # a single episode's realized total, not lifetime account P&L.
                with localcontext() as ctx:
                    ctx.prec = 50
                    totals = {
                        key: sum(
                            (
                                Decimal(getattr(item, key))
                                for item in fills
                                if getattr(item, key) is not None
                            ),
                            Decimal(0),
                        )
                        for key in ("gross", "cost", "net", "fee", "slippage")
                    }
                if (
                    outcome.gross_pnl,
                    outcome.cost,
                    outcome.net_pnl,
                    outcome.fee,
                    outcome.slippage,
                ) != (
                    number(totals["gross"]),
                    number(totals["cost"]),
                    number(totals["net"]),
                    number(totals["fee"]),
                    number(totals["slippage"]),
                ) or (episode.realized_gross, episode.realized_cost, episode.realized_net) != (
                    outcome.gross_pnl,
                    outcome.cost,
                    outcome.net_pnl,
                ):
                    raise ValueError("execution_outcome_episode_totals_mismatch")
                for item in fills:
                    linked = repository.result(item.intent_ref.removeprefix("intent:"))
                    if (
                        linked is None
                        or linked.fill_ref != item.id
                        or linked.fill_hash != item.payload_hash
                    ):
                        raise ValueError("execution_outcome_episode_fill_receipt_mismatch")
                    prior_intent = repository.intent(item.intent_ref.removeprefix("intent:"))
                    item_quote = QuoteRepository(archive).read(session, quote_id=item.quote_id)
                    if (
                        item_quote.payload_hash != item.quote_hash
                        or item_quote.availability_hash != item.availability_hash
                    ):
                        raise ValueError("execution_outcome_episode_quote_mismatch")
                    _validate_quote_for_intent(item_quote, protocol, prior_intent, item.executed_at)
                    _verify_fill_quote(item, item_quote, protocol)
            from packages.kernel.decision_hub_kernel.application.action_review import (
                read_published_branch_in_session,
            )

            decision, sidecar, candidate = read_published_branch_in_session(
                session, revision, observed
            )
            research_originals = {
                "revision": revision,
                "decision": decision,
                "sidecar": sidecar,
                "candidate": candidate,
            }
            return outcome, evaluation, risk_evaluation, result, research_originals

        prepared = prepare_originals(self.database, read)
        outcome, evaluation, risk_evaluation, result, research_originals = prepared.value
        archive.verify()
        if evaluation:
            evaluation.verify_bytes(self.store)
        if risk_evaluation is not evaluation:
            risk_evaluation.verify_bytes(self.store)
        value = {
            "outcome": outcome,
            "execution_receipt": result,
            "available_at": self._now(),
            "financial_qualified": False,
            "research_originals": research_originals,
        }
        return PreparedActionOutcomeRead(
            value, prepared, archive, evaluation, risk_evaluation, self.store
        )

@dataclass(frozen=True)
class PreparedActionOutcomeRead:
    """Per-call server preparation; input to a short writer, never caller authority.

    Exact existing financial authentication remains in prepare_outcome. A writer
    must also enforce its own purpose, current owner/lease and CAS. No permission
    is transferred by the public value returned to clients.
    """

    value: dict
    originals: object
    archive: object
    evaluation: object
    risk_evaluation: object
    store: object

    def verify_bytes(self):
        self.archive.verify()
        if self.evaluation:
            self.evaluation.verify_bytes(self.store)
        if self.risk_evaluation is not self.evaluation:
            self.risk_evaluation.verify_bytes(self.store)

    def verify(self, session):
        self.originals.verify(session)
        self.verify_bytes()

    def verify_after_writes(self, session):
        self.originals.verify_after_writes(session)
        self.verify_bytes()
