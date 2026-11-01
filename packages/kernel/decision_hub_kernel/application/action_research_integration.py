"""Event opportunity registration and bounded, report-independent Action hooks.

This is an internal composition service. It accepts only committed identities,
never a caller plan or a verified flag. The publication service repeats native
authentication. Retrying this hook never executes the research model.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchTaskReceiptV2,
)

from ..decision.action_research_policy import research_action_catalog
from ..persistence.action_records import DecisionOpportunityRecord
from ..persistence.action_repository import ActionRepository
from ..persistence.db import (
    Database,
    RunEventRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from ..persistence.original_preparation import begin_immediate
from ..ports.action_plan import Opportunity
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_synthesis_protocol import DECISION_OUTPUT_SCHEMA_REF
from .action_publication import ActionPublicationService
from .action_research_reader import (
    ActionFinalSynthesisAbsent,
    reject_final_without_action_synthesis_in_session,
)
from .commit import PUBLICATION_SOURCE_ROLE
from .research_binding import TASK_ROLE, TASK_SCHEMA
from .research_graph_binding import GraphFinalProjectionRef, parse_graph_final_ref
from .research_run_scope import read_run_spot_admission_in_session

LOGGER = logging.getLogger(__name__)
REGISTRATION_ROLE = "action_opportunity_registration"
REGISTRATION_SCHEMA = "action-opportunity-registration.v1"
HOOK_SCHEMA = "action-publication-hook.v1"
MAX_AUTO_ATTEMPTS = 3
MAX_RECONCILE_CANDIDATES = 3
RETRY_SECONDS = 300
HORIZONS = {"30m": 1800, "24h": 86400, "72h": 259200}


def _json(body: object) -> str:
    return json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _row(session: Session, run_id: str, role: str) -> RunRoleOutputRecord | None:
    return session.scalar(
        select(RunRoleOutputRecord)
        .filter_by(run_id=run_id, role=role)
        .execution_options(populate_existing=True)
    )


def _body(row: RunRoleOutputRecord) -> dict[str, Any]:
    body = json.loads(row.payload_json)
    if (
        not isinstance(body, dict)
        or _json(body) != row.payload_json
        or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash
        or body.get("schema_version") != row.schema_version
        or body.get("run_id") != row.run_id
    ):
        raise ValueError("action_hook_original_invalid")
    return body


def _save(session: Session, run_id: str, role: str, body: dict[str, Any], now: datetime) -> None:
    payload = _json(body)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    previous = _row(session, run_id, role)
    if previous is not None:
        if previous.payload_json != payload or previous.payload_hash != digest:
            raise ValueError("action_hook_original_conflict")
        return
    session.add(
        RunRoleOutputRecord(
            output_id="actionhook_" + canonical_hash([run_id, role])[:40],
            run_id=run_id,
            role=role,
            payload_json=payload,
            payload_hash=digest,
            schema_version=body["schema_version"],
            created_at=now,
        )
    )
    session.flush()


def _event(session: Session, run_id: str, kind: str, body: dict[str, Any], now: datetime) -> None:
    sequence = (
        session.scalar(select(func.max(RunEventRecord.sequence_no)).filter_by(run_id=run_id)) or 0
    )
    session.add(
        RunEventRecord(
            run_id=run_id,
            sequence_no=sequence + 1,
            event_type=kind,
            occurred_at=now,
            payload_json=_json(body),
        )
    )


def _task(session: Session, run_id: str) -> ResearchTaskReceiptV2:
    row = _row(session, run_id, TASK_ROLE)
    if row is None or row.schema_version != TASK_SCHEMA:
        raise ValueError("action_hook_frozen_task_required")
    value = ResearchTaskReceiptV2.model_validate(_body(row))
    if value.request_hash != canonical_hash(value.request.model_dump(mode="json")):
        raise ValueError("action_hook_task_hash_invalid")
    return value


def register_action_opportunities_in_session(
    session: Session,
    *,
    request: ResearchSessionRequest,
    pack_definition_hash: str,
    compiled_policy_recipe_hash: str,
    registered_at: datetime,
) -> tuple[Opportunity, ...]:
    """Only the first current-decision spot Task writer calls this function.

    It creates event opportunities, not the pre-registered A28 hourly denominator.
    The entire Task/opportunity transaction rolls back if either side fails.
    """
    if (
        request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
        or request.research_mode != "current_decision"
    ):
        return ()
    from .action_review import review_for_run_in_session

    if review_for_run_in_session(session, request.run_id) is not None:
        return ()  # Original opportunity only; W3.2 owns revision publication.
    now = aware_utc(registered_at)
    task = _task(session, request.run_id)
    if (
        task.request.model_dump(mode="json") != request.model_dump(mode="json")
        or task.pack_definition_hash != pack_definition_hash
    ):
        raise ValueError("action_hook_task_binding_mismatch")
    binding = read_run_spot_admission_in_session(session, request.run_id)
    if binding is None:
        raise ValueError("action_hook_spot_admission_required")
    catalog = research_action_catalog(binding.scope.instrument)
    if catalog["scope_id"] != binding.scope.scope_id or request.deadline_at <= now:
        raise ValueError("action_hook_registration_time_or_scope_invalid")
    recipe = {
        "schema_version": "action-event-recipe.v1",
        "pack_definition_hash": pack_definition_hash,
        "compiled_policy_recipe_hash": compiled_policy_recipe_hash,
        "action_method": "research-action-catalog.v1",
        "action_catalog_hash": canonical_hash(catalog),
    }
    for digest in (pack_definition_hash, compiled_policy_recipe_hash):
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("action_hook_recipe_hash_invalid")
    opportunities = tuple(
        Opportunity.model_validate(
            {
                "schema_version": "decision-opportunity.v1",
                "id": "actionop_" + canonical_hash([request.run_id, horizon])[:40],
                "key": "action-event:" + request.run_id + ":" + horizon,
                "scope_id": binding.scope.scope_id,
                "horizon": horizon,
                "kind": "event",
                "event_ref": request.event_id,
                "registered_at": now,
                "time_start": now,
                "time_end": request.deadline_at + timedelta(seconds=duration),
                "target_spec_hash": catalog["targets"][horizon]["hash"],
                "recipe_hash": canonical_hash(recipe),
                "cohort": "prospective" if request.execution_mode == "live" else "diagnostic",
                "status": "open",
                "reason": None,
            }
        )
        for horizon, duration in HORIZONS.items()
    )
    body = {
        "schema_version": REGISTRATION_SCHEMA,
        "run_id": request.run_id,
        "event_id": request.event_id,
        "task_request_hash": task.request_hash,
        "registered_at": now.isoformat(),
        "recipe": recipe,
        "window_policy": "task_deadline_plus_horizon.v1",
        "deadline_delta_seconds": (request.deadline_at - now).total_seconds(),
        "opportunities": [item.model_dump(mode="json") for item in opportunities],
    }
    previous = _row(session, request.run_id, REGISTRATION_ROLE)
    if previous is not None and _body(previous) != body:
        raise ValueError("action_hook_registration_immutable")
    repository = ActionRepository(session)
    for item in opportunities:
        repository.register_opportunity(item)
    _save(session, request.run_id, REGISTRATION_ROLE, body, now)
    return opportunities


def _registration(session: Session, run_id: str) -> tuple[Opportunity, ...]:
    from .action_review import review_for_run_in_session

    task_row = _row(session, run_id, TASK_ROLE)
    task = _task(session, run_id) if task_row is not None and task_row.schema_version == TASK_SCHEMA else None
    from .perpetual_research_binding import (
        ADMISSION_ROLE,
        OUTPUT_SCHEMAS,
        read_perpetual_admission_identity_in_session,
    )
    admission_row = _row(session, run_id, ADMISSION_ROLE)
    if admission_row is not None:
        if task is not None and task.request.output_schema_ref not in OUTPUT_SCHEMAS:
            raise ValueError('perpetual_action_hook_task_mismatch')
        # A genuine failed/expired admission can precede Task creation. Its
        # denominator still needs closure; this SQL identity grants no publish.
        saved = read_perpetual_admission_identity_in_session(session, run_id)
        if saved is None:
            raise ValueError('perpetual_action_hook_admission_missing')
        admitted, _reference = saved
        original = ActionRepository(session).get_opportunity(admitted['opportunity_id'])
        if original is None or original.model_dump(mode='json', exclude={'status', 'reason'}) != {
            key: value for key, value in admitted['opportunity'].items()
            if key not in {'status', 'reason'}
        }:
            raise ValueError('perpetual_action_hook_opportunity_original_changed')
        return (original,)
    if task is not None and task.request.output_schema_ref in OUTPUT_SCHEMAS:
        raise ValueError('perpetual_action_hook_admission_missing')
    review = review_for_run_in_session(session, run_id)
    if review is not None:
        original = ActionRepository(session).get_opportunity(review.opportunity.id)
        if original is None:
            raise ValueError("action_review_opportunity_missing")
        return (original,)
    row = _row(session, run_id, REGISTRATION_ROLE)
    if row is None:
        return ()
    body = _body(row)
    task = _task(session, run_id)
    if (
        row.schema_version != REGISTRATION_SCHEMA
        or body["task_request_hash"] != task.request_hash
        or task.request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
        or task.request.research_mode != "current_decision"
        or body["event_id"] != task.request.event_id
    ):
        raise ValueError("action_hook_registration_task_mismatch")
    saved = tuple(Opportunity.model_validate(item) for item in body["opportunities"])
    if {item.horizon for item in saved} != set(HORIZONS) or len(saved) != 3:
        raise ValueError("action_hook_registration_horizons_invalid")
    repository = ActionRepository(session)
    originals = []
    for item in saved:
        original = repository.get_opportunity(item.id)
        if original is None or original.model_dump(exclude={"status", "reason"}) != item.model_dump(
            exclude={"status", "reason"}
        ):
            raise ValueError("action_hook_opportunity_original_changed")
        originals.append(original)
    return tuple(originals)


def _hook_role(horizon: str, suffix: str) -> str:
    return "action_hook_" + horizon + "_" + suffix


def _error(error: Exception) -> str:
    code = getattr(error, "error_code", None) or str(error)
    return (
        code
        if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_:.-]{1,160}", code)
        else type(error).__name__
    )


class ActionResearchIntegration:
    def __init__(
        self,
        database: Database,
        *,
        publisher: ActionPublicationService | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database, self.clock = database, clock
        self.publisher = publisher or ActionPublicationService(database, clock=clock)

    def _identities(self, session: Session, run_id: str) -> dict[str, Any] | None:
        run = session.get(RunRecord, run_id, populate_existing=True)
        row = _row(session, run_id, PUBLICATION_SOURCE_ROLE)
        if (
            run is None
            or run.status not in {"completed", "degraded"}
            or run.artifact_id is None
            or row is None
        ):
            return None
        body = _body(row)
        finished_at = as_utc(run.finished_at)
        if (
            body["event_id"] != run.event_id
            or body["artifact_id"] != run.artifact_id
            or row.output_id != "publication_" + row.payload_hash[:40]
            or finished_at is None
            or body["recorded_at"] != finished_at.isoformat()
        ):
            raise ValueError("action_hook_committed_identity_invalid")
        # This locator is only a candidate identity. Publisher repeats full proof.
        return {
            "run_id": run_id,
            "event_id": run.event_id,
            "artifact_id": run.artifact_id,
            "reference": parse_graph_final_ref(body["graph_final_ref"]),
        }

    def publish_committed(
        self, *, run_id: str, event_id: str, artifact_id: str, reference: GraphFinalProjectionRef
    ) -> tuple[dict[str, Any], ...]:
        with self.database.session() as session:
            opportunities = _registration(session, run_id)
            if not opportunities:
                return ()
            actual = self._identities(session, run_id)
            supplied = dict(
                run_id=run_id, event_id=event_id, artifact_id=artifact_id, reference=reference
            )
            if actual != supplied:
                raise ValueError("action_hook_committed_identity_mismatch")
        return tuple(self._attempt(supplied, item) for item in opportunities)

    def _attempt(self, identity: dict[str, Any], opportunity: Opportunity) -> dict[str, Any]:
        run_id, horizon = identity["run_id"], opportunity.horizon
        now = aware_utc(self.clock())
        key = "publish_" + canonical_hash([run_id, opportunity.id])
        command_hash = canonical_hash(
            {
                **identity,
                "reference": identity["reference"].model_dump(mode="json"),
                "opportunity_id": opportunity.id,
            }
        )
        with self.database.session() as session:
            begin_immediate(session)
            repository = ActionRepository(session)
            success = _row(session, run_id, _hook_role(horizon, "success"))
            if success is not None:
                saved = _body(success)
                if saved["command_hash"] != command_hash:
                    raise ValueError("action_hook_key_conflict")
                return saved
            receipt = repository.get_command("system:action-publication:v1", key)
            if receipt is not None:
                if receipt.payload_hash != command_hash or receipt.operation != "publish":
                    raise ValueError("action_hook_key_conflict")
                result = self._result(
                    identity,
                    opportunity,
                    key,
                    command_hash,
                    0,
                    "published",
                    now,
                    receipt_ref=receipt.request_id,
                    receipt_hash=receipt.receipt_hash,
                    publication_status=receipt.response["status"],
                )
                _save(session, run_id, _hook_role(horizon, "success"), result, now)
                _event(session, run_id, "action.publication.recovered", result, now)
                return result
            exhausted = _row(session, run_id, _hook_role(horizon, "exhausted"))
            if exhausted is not None:
                return _body(exhausted)
            attempts = [
                row
                for n in range(1, MAX_AUTO_ATTEMPTS + 1)
                if (row := _row(session, run_id, _hook_role(horizon, f"{n}.started"))) is not None
            ]
            if attempts:
                last = _body(attempts[-1])
                if last["command_hash"] != command_hash:
                    raise ValueError("action_hook_key_conflict")
                if now < opportunity.time_end and datetime.fromisoformat(last["retry_after"]) > now:
                    return {
                        "status": "deferred",
                        "run_id": run_id,
                        "opportunity_id": opportunity.id,
                        "request_id": key,
                    }
            attempt = len(attempts) + 1
            if attempt > MAX_AUTO_ATTEMPTS or now >= opportunity.time_end:
                result = self._result(
                    identity,
                    opportunity,
                    key,
                    command_hash,
                    len(attempts),
                    "exhausted" if attempt > MAX_AUTO_ATTEMPTS else "expired",
                    now,
                )
                _save(session, run_id, _hook_role(horizon, "exhausted"), result, now)
                _event(session, run_id, "action.publication." + result["status"], result, now)
                original = repository.get_opportunity(opportunity.id)
                if original is not None and original.status == "open":
                    repository.set_opportunity_status(
                        opportunity.id,
                        expected_status="open",
                        status="missed",
                        reason="action_hook_window_elapsed"
                        if now >= opportunity.time_end
                        else "action_hook_attempt_budget_exhausted",
                    )
                return result
            started = self._result(
                identity, opportunity, key, command_hash, attempt, "started", now
            )
            started["retry_after"] = (now + timedelta(seconds=RETRY_SECONDS * attempt)).isoformat()
            _save(session, run_id, _hook_role(horizon, f"{attempt}.started"), started, now)
            _event(session, run_id, "action.publication.started", started, now)
        try:
            receipt = self.publisher.publish(**identity, opportunity_id=opportunity.id)
            result = self._result(
                identity,
                opportunity,
                key,
                command_hash,
                attempt,
                "published",
                aware_utc(self.clock()),
                receipt_ref=receipt.request_id,
                receipt_hash=receipt.receipt_hash,
                publication_status=receipt.response["status"],
            )
        except Exception as error:
            result = self._result(
                identity,
                opportunity,
                key,
                command_hash,
                attempt,
                "failed",
                aware_utc(self.clock()),
                error_code=_error(error),
                **({"retryable": False, "terminal_reason": "immutable_final_has_no_synthesis"}
                   if isinstance(error, ActionFinalSynthesisAbsent) else {}),
            )
        # Publication may have committed even if this audit write fails. Its
        # original stable receipt is recovered before any new attempt next time.
        try:
            with self.database.session() as session:
                begin_immediate(session)
                if result.get("retryable") is False:
                    # No durable terminal classification from a stale read.
                    # This repeats only exact locator/bytes checks, not Graph replay.
                    try:
                        reject_final_without_action_synthesis_in_session(session, **identity)
                    except ActionFinalSynthesisAbsent:
                        pass
                    else:
                        raise ValueError("action_final_absence_changed")
                _save(
                    session,
                    run_id,
                    _hook_role(horizon, f"{attempt}.result"),
                    result,
                    aware_utc(self.clock()),
                )
                if result["status"] == "published":
                    _save(
                        session,
                        run_id,
                        _hook_role(horizon, "success"),
                        result,
                        aware_utc(self.clock()),
                    )
                elif result.get("retryable") is False or attempt == MAX_AUTO_ATTEMPTS:
                    terminal = {**result, "status": "exhausted"}
                    _save(
                        session,
                        run_id,
                        _hook_role(horizon, "exhausted"),
                        terminal,
                        aware_utc(self.clock()),
                    )
                    repository = ActionRepository(session)
                    original = repository.get_opportunity(opportunity.id)
                    if original is not None and original.status == "open":
                        repository.set_opportunity_status(
                            opportunity.id,
                            expected_status="open",
                            status="missed",
                            reason=("action_hook_immutable_final_unpublishable:"
                                    if result.get("retryable") is False
                                    else "action_hook_attempt_budget_exhausted:")
                            + result["error_code"],
                        )
                    _event(
                        session,
                        run_id,
                        "action.publication.exhausted",
                        terminal,
                        aware_utc(self.clock()),
                    )
                _event(
                    session,
                    run_id,
                    "action.publication." + result["status"],
                    result,
                    aware_utc(self.clock()),
                )
        except Exception as error:
            LOGGER.error(
                "action_hook_audit_storage_unavailable run=%s opportunity=%s key=%s error=%s",
                run_id,
                opportunity.id,
                key,
                _error(error),
            )
        return result

    @staticmethod
    def _result(
        identity: dict[str, Any],
        opportunity: Opportunity,
        key: str,
        command_hash: str,
        attempt: int,
        status: str,
        now: datetime,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": HOOK_SCHEMA,
            **identity,
            "reference": identity["reference"].model_dump(mode="json"),
            "opportunity_id": opportunity.id,
            "horizon": opportunity.horizon,
            "request_id": key,
            "command_hash": command_hash,
            "attempt": attempt,
            "max_auto_attempts": MAX_AUTO_ATTEMPTS,
            "status": status,
            "recorded_at": now.isoformat(),
            **extra,
        }

    def reconcile_missed_hooks(
        self, *, max_candidates: int = MAX_RECONCILE_CANDIDATES
    ) -> tuple[dict[str, Any], ...]:
        """At most three saved opportunities per idle tick; no new process/loop."""
        if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_RECONCILE_CANDIDATES:
            raise ValueError("action_hook_batch_limit_invalid")
        now = aware_utc(self.clock())
        ledger, marker, attempt_row = (
            aliased(RunRoleOutputRecord),
            aliased(RunRoleOutputRecord),
            aliased(RunRoleOutputRecord),
        )
        perpetual = aliased(RunRoleOutputRecord)
        from ..persistence.risk_research_records import (
            RiskResearchRequestRecord,
            RiskResearchTriggerRecord,
        )

        review_request, review_trigger = (
            aliased(RiskResearchRequestRecord),
            aliased(RiskResearchTriggerRecord),
        )
        opportunity = DecisionOpportunityRecord
        with self.database.session() as session:
            query = (
                select(RunRecord.run_id, opportunity.opportunity_id)
                .outerjoin(
                    ledger, (ledger.run_id == RunRecord.run_id) & (ledger.role == REGISTRATION_ROLE)
                )
                .outerjoin(
                    perpetual, (perpetual.run_id == RunRecord.run_id)
                    & (perpetual.role == 'perpetual_research_admission_v1')
                    & (perpetual.schema_version == 'perpetual-research-admission.v1'),
                )
                .outerjoin(review_request, review_request.run_id == RunRecord.run_id)
                .outerjoin(
                    review_trigger,
                    (review_trigger.request_id == review_request.request_id)
                    & (review_trigger.kind == "action_review"),
                )
                .join(
                    opportunity,
                    or_(
                        (perpetual.output_id.is_not(None))
                        & (opportunity.schema_version == 'decision-opportunity.v2')
                        & (opportunity.opportunity_id == func.json_extract(
                            perpetual.payload_json, '$.opportunity_id'
                        )),
                        (ledger.output_id.is_not(None))
                        & (
                            opportunity.key
                            == "action-event:" + RunRecord.run_id + ":" + opportunity.horizon
                        ),
                        (review_trigger.trigger_key.is_not(None))
                        & (
                            opportunity.opportunity_id
                            == func.json_extract(
                                review_trigger.payload_json, "$.source.review.opportunity.id"
                            )
                        ),
                    ),
                )
                .where(
                    or_(
                        RunRecord.status.in_(("failed", "cancelled")),
                        opportunity.end_at <= now,
                        RunRecord.status.in_(("completed", "degraded"))
                        & RunRecord.artifact_id.is_not(None),
                    ),
                    opportunity.status.in_(("open", "decided")),
                )
                .where(
                    ~exists(
                        select(marker.output_id).where(
                            marker.run_id == RunRecord.run_id,
                            marker.role.in_(
                                (
                                    "action_hook_" + opportunity.horizon + "_success",
                                    "action_hook_" + opportunity.horizon + "_exhausted",
                                )
                            ),
                        )
                    )
                )
                .where(
                    or_(
                        RunRecord.status.in_(("failed", "cancelled")),
                        opportunity.end_at <= now,
                        ~exists(
                            select(attempt_row.output_id).where(
                                attempt_row.run_id == RunRecord.run_id,
                                attempt_row.role.like(
                                    "action_hook_" + opportunity.horizon + "_%.started"
                                ),
                                func.json_extract(attempt_row.payload_json, "$.retry_after")
                                > now.isoformat(),
                            )
                        ),
                    )
                )
                .order_by(opportunity.registered_at, opportunity.opportunity_id)
                .limit(max_candidates)
            )
            candidates = list(session.execute(query))
        output = []
        for run_id, opportunity_id in candidates:
            try:
                with self.database.session() as session:
                    registered = _registration(session, run_id)
                    identity = self._identities(session, run_id)
                    selected = next(item for item in registered if item.id == opportunity_id)
                if identity is not None:
                    output.append(self._attempt(identity, selected))
                else:
                    disposition = self._dispose_without_publication(run_id, selected.id)
                    if disposition is not None:
                        output.append(disposition)
            except Exception as error:
                LOGGER.error(
                    "action_hook_reconcile_failed run=%s opportunity=%s error=%s",
                    run_id,
                    opportunity_id,
                    _error(error),
                )
                output.append(
                    {
                        "run_id": run_id,
                        "opportunity_id": opportunity_id,
                        "status": "failed",
                        "error_code": _error(error),
                    }
                )
        return tuple(output)

    def _dispose_without_publication(
        self, run_id: str, opportunity_id: str
    ) -> dict[str, Any] | None:
        """Close a registered denominator when no Report can supply an Action."""
        now = aware_utc(self.clock())
        with self.database.session() as session:
            begin_immediate(session)
            selected = next(
                item for item in _registration(session, run_id) if item.id == opportunity_id
            )
            run = session.get(RunRecord, run_id, populate_existing=True)
            if run is None or selected.status != "open":
                return None
            # A concurrent Report commit must pass the original publisher path.
            if self._identities(session, run_id) is not None:
                return None
            if run.status in {"failed", "cancelled"}:
                status, reason = "missed", "research_run_" + run.status
            elif now >= selected.time_end:
                status, reason = "expired", "action_hook_window_elapsed_without_publication"
            else:
                return None
            result = {
                "schema_version": HOOK_SCHEMA,
                "run_id": run_id,
                "event_id": run.event_id,
                "artifact_id": run.artifact_id,
                "opportunity_id": selected.id,
                "horizon": selected.horizon,
                "request_id": "publish_" + canonical_hash([run_id, selected.id]),
                "status": status,
                "reason": reason,
                "research_error_code": run.error_code,
                "attempt": 0,
                "max_auto_attempts": MAX_AUTO_ATTEMPTS,
                "recorded_at": now.isoformat(),
            }
            ActionRepository(session).set_opportunity_status(
                selected.id, expected_status="open", status=status, reason=reason
            )
            _save(session, run_id, _hook_role(selected.horizon, "exhausted"), result, now)
            _event(session, run_id, "action.publication." + status, result, now)
            return result

