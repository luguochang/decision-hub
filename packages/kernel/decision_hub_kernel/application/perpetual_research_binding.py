"""Immutable, owner-bound inputs for one genuine perpetual research target.

Existing Run originals hold the admission before Task creation and the exact Task
binding afterward. Archive reads, source authentication and close calculations
run outside writers. Neither reference grants financial qualification or orders.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ResearchExecutionRequestReceiptV3,
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionActionContextV2,
    DecisionOpportunityV2,
    DecisionSettingsCommandV1,
    DecisionSettingsPartitionV1,
    DecisionSourceRefV1,
    PendingPublicationTargetV1,
    PublicationAnchorRuleV1,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from .decision_execution_profile import read_execution_profile
from .decision_settings import DecisionSettingsService
from .decision_settings_binding import DecisionSettingsBindingReader
from .okx_perpetual_source import OKXPerpetualOriginalReader, OKXPerpetualSource
from .perpetual_close_source import PerpetualCloseSource, read_perpetual_close_window_in_session
from .perpetual_research_horizon import perpetual_horizon_seconds, require_perpetual_horizon

ADMISSION_ROLE = "perpetual_research_admission_v1"
TASK_BINDING_ROLE = "perpetual_research_task_binding_v1"
OUTPUT_SCHEMA = "decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2"
EVENT_OUTPUT_SCHEMA = "decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3"
OUTPUT_SCHEMAS = {OUTPUT_SCHEMA, EVENT_OUTPUT_SCHEMA}
_METHOD_FILES = (
    "decision/action_comparison.py",
    "decision/perpetual_accounting.py",
    "decision/perpetual_publication.py",
    "application/perpetual_cost_assumptions.py",
    "ports/exact_decimal_text.py",
)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _json_value(value):
    if isinstance(value, datetime):
        return aware_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _ref(name, body):
    return {"ref": name, "hash": canonical_hash(body)}


def _method_body(dependencies):
    return dict(
        schema_version="perpetual-comparison-method.v1",
        qualification="research_trial",
        financial_qualified=False,
        selection_kind="assumed_unit_mean_net",
        dependencies=dependencies,
        method_code_hash=canonical_hash(dependencies),
        executable_quantity_supported=False,
        required_inputs=[
            "complete_path_distribution",
            "explicit_fill_weight_assumption",
            "explicit_costs",
            "original_settings",
        ],
    )


def comparison_method_original(*, include_source=True):
    kernel = Path(__file__).resolve().parents[1]
    # Hash and render the same bytes, including original line endings.
    sources = {name: (kernel / name).read_bytes() for name in _METHOD_FILES}
    dependencies = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
    code_hash = canonical_hash(dependencies)
    body = _method_body(dependencies)
    if include_source:
        body["source_bundle"] = dict(
            schema_version="perpetual-method-source-bundle.v1",
            content_kind="immutable_code_data",
            files={
                name: dict(sha256=dependencies[name], source_text=raw.decode("utf-8"))
                for name, raw in sources.items()
            },
        )
    return body, _ref("decision-method:perpetual-assumed-unit-mean-net.v1", body), code_hash


def _frozen_comparison_method(admission):
    """Verify archived code data, never substitute today's source bytes.

    Prepared execution/Graph readers still enforce retained implementation
    availability independently. This authenticates the method's frozen body.
    """
    method = admission["comparison_method"]
    dependencies = method["dependencies"]
    if set(dependencies) != set(_METHOD_FILES):
        raise ValueError("perpetual_research_method_original_changed")
    expected = _method_body(dependencies)
    if "source_bundle" in method:
        bundle = method["source_bundle"]
        if (
            set(bundle) != {"schema_version", "content_kind", "files"}
            or bundle["schema_version"] != "perpetual-method-source-bundle.v1"
            or bundle["content_kind"] != "immutable_code_data"
            or set(bundle["files"]) != set(_METHOD_FILES)
        ):
            raise ValueError("perpetual_research_method_original_changed")
        for name, item in bundle["files"].items():
            if (
                set(item) != {"sha256", "source_text"}
                or item["sha256"] != dependencies[name]
                or hashlib.sha256(item["source_text"].encode()).hexdigest() != item["sha256"]
            ):
                raise ValueError("perpetual_research_method_original_changed")
        expected["source_bundle"] = bundle
    if method != expected:
        raise ValueError("perpetual_research_method_original_changed")
    return (
        method,
        _ref("decision-method:perpetual-assumed-unit-mean-net.v1", method),
        canonical_hash(dependencies),
    )


def _row(session, run_id, role):
    return session.scalars(
        select(RunRoleOutputRecord)
        .where(RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == role)
        .execution_options(populate_existing=True)
    ).one_or_none()


def _read(session, run_id, role):
    row = _row(session, run_id, role)
    if row is None:
        raise ValueError("perpetual_research_" + role + "_missing")
    body = json.loads(row.payload_json)
    prefix = "perp_admission_" if role == ADMISSION_ROLE else "perp_task_binding_"
    if (
        canonical(body) != row.payload_json
        or canonical_hash(body) != row.payload_hash
        or row.schema_version != body["schema_version"]
        or body["run_id"] != run_id
        or row.output_id != prefix + canonical_hash(run_id)
        or as_utc(row.created_at) != datetime.fromisoformat(body["recorded_at"])
    ):
        raise ValueError("perpetual_research_original_invalid")
    return body, {"ref": row.output_id, "hash": row.payload_hash}


def _save(session, run_id, role, body):
    old = _row(session, run_id, role)
    if old is not None:
        original, ref = _read(session, run_id, role)
        if original != body:
            raise ValueError("perpetual_research_original_conflict")
        return DecisionSourceRefV1.model_validate(ref)
    prefix = "perp_admission_" if role == ADMISSION_ROLE else "perp_task_binding_"
    ref = DecisionSourceRefV1(ref=prefix + canonical_hash(run_id), hash=canonical_hash(body))
    session.add(
        RunRoleOutputRecord(
            output_id=ref.ref,
            run_id=run_id,
            role=role,
            schema_version=body["schema_version"],
            payload_json=canonical(body),
            payload_hash=ref.hash,
            created_at=datetime.fromisoformat(body["recorded_at"]),
        )
    )
    return ref


def _run(session, run_id, *, event_id=None):
    run = session.get(RunRecord, run_id, populate_existing=True)
    study = (
        session.get(StudyRecord, run.study_id, populate_existing=True)
        if run and run.study_id
        else None
    )
    if (
        run is None
        or study is None
        or run.strategy_version != "research.v1"
        or event_id is not None
        and run.event_id != event_id
        or study.market != "crypto"
        or study.instrument not in {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
        or study.identity_basis != "exact_perpetual_decision_v2"
        or study.horizon_scope not in {"4h", "24h", "72h"}
    ):
        raise ValueError("perpetual_research_run_scope_invalid")
    return run, study


def _settings_original(session, owner, partition):
    current = DecisionSettingsService.current_in_session(session, owner, partition)
    if current is None or current.revision.operation != "upsert" or current.revision.values is None:
        raise ValueError("perpetual_research_settings_unavailable")
    utility = current.revision.values.utility_policy.model_dump(mode="json")
    if utility["hash"] != canonical_hash(
        {key: value for key, value in utility.items() if key != "hash"}
    ):
        raise ValueError("perpetual_research_utility_original_hash_invalid")
    return current


def _window_json(window):
    return _json_value(asdict(window))


def _native_window(window):
    """All real prices and row locators, with repeated page metadata interned."""
    from ..ports.perpetual_close import POLICY

    pages = []
    indices = {}
    locators = []
    for item in window["closes"]:
        page = dict(
            capture_id=item["capture_id"],
            capture_hash=item["capture_hash"],
            capture_metadata_hash=item["capture_metadata_hash"],
            body_ref=item["body_ref"],
            received_at=item["received_at"],
            recorded_at=item["recorded_at"],
        )
        if item["capture_id"] not in indices:
            indices[item["capture_id"]] = len(pages)
            pages.append(page)
        locators.append([indices[item["capture_id"]], item["row_index"]])
    return {
        **{key: value for key, value in window.items() if key not in {"closes", "log_returns"}},
        "price_series": dict(
            schema_version="confirmed-perpetual-close-series.v1",
            first_close_at=window["closes"][0]["close_at"],
            last_close_at=window["closes"][-1]["close_at"],
            grid_seconds=60,
            prices=[item["price"] for item in window["closes"]],
            page_originals=pages,
            row_locators=locators,
        ),
        "threshold_method": dict(policy=POLICY, software_hash=window["software_hash"]),
    }


def _require_admission_horizon(admission, study):
    horizon = require_perpetual_horizon(study.horizon_scope)
    duration = perpetual_horizon_seconds(horizon)
    target = PendingPublicationTargetV1.model_validate(admission["candidate_target"])
    rule = PublicationAnchorRuleV1.model_validate(admission["anchor_rule"])
    threshold = next(
        row for row in admission["close_window"]["thresholds"] if row["horizon"] == horizon
    )
    if (
        admission["context"]["horizon"] != horizon
        or target.horizon != horizon
        or rule.horizon != horizon
        or target.duration_seconds != duration
        or rule.duration_seconds != duration
        or target.threshold_fraction != threshold["fraction"]
        or rule.threshold_fraction != target.threshold_fraction
        or admission["opportunity"]["horizon"] != horizon
        or admission["opportunity"]["time_end"] != admission["candidate_target"]["valid_until"]
        or admission["valid_until"] != admission["candidate_target"]["valid_until"]
        or admission["candidate_target_ref"]["hash"]
        != canonical_hash(admission["candidate_target"])
        or admission["anchor_rule_ref"]["hash"] != canonical_hash(admission["anchor_rule"])
        or rule.candidate_target_ref.model_dump(mode="json") != admission["candidate_target_ref"]
    ):
        raise ValueError("perpetual_research_horizon_original_mismatch")


def _authenticate_admission(session, admission, *, purpose="active_research"):
    if purpose not in {"active_research", "frozen_read"}:
        raise ValueError("perpetual_research_input_purpose_invalid")
    run, study = _run(session, admission["run_id"], event_id=admission["event_id"])
    _require_admission_horizon(admission, study)
    from .perpetual_research_horizon import authenticate_perpetual_deadlines

    authenticate_perpetual_deadlines(admission)
    from .perpetual_research_schedule import authenticate_automatic_admission_in_session

    authenticate_automatic_admission_in_session(session, admission)
    partition = DecisionSettingsPartitionV1.model_validate(admission["partition"])
    if (
        run.study_id != admission["study_id"]
        or partition.instrument_id != "okx:" + study.instrument
    ):
        raise ValueError("perpetual_research_study_changed")
    from ..persistence.action_repository import ActionRepository

    opportunity = ActionRepository(session).get_opportunity(admission["opportunity_id"])
    if opportunity is None or opportunity.model_dump(mode="json", exclude={"status", "reason"}) != {
        key: value
        for key, value in admission["opportunity"].items()
        if key not in {"status", "reason"}
    }:
        raise ValueError("perpetual_research_opportunity_original_changed")
    if purpose == "active_research":
        current = _settings_original(session, admission["owner_id"], partition)
        saved, state_version = current.revision, current.state_version
    else:
        saved = DecisionSettingsService._revision(
            session, admission["settings_revision"]["revision_id"]
        )
        state_version = saved.revision_no
    if (
        saved.model_dump(mode="json") != admission["settings_revision"]
        or state_version != admission["settings_state_version"]
        or saved.owner_id != admission["owner_id"]
        or saved.partition != partition
    ):
        raise ValueError("perpetual_research_settings_head_changed")
    from .perpetual_research_inputs import read_effective_inputs_in_session

    values = read_effective_inputs_in_session(session, admission, purpose=purpose).effective_values
    source = OKXPerpetualOriginalReader(RawBodyArchive(Path(admission["archive_root"])))
    if "event_agenda" in admission:
        from .perpetual_event_agenda import read_event_agenda_in_session

        agenda = read_event_agenda_in_session(
            session, admission["event_agenda"], archive=source.archive
        )
        if (
            agenda.instrument_refs != [values.instrument]
            or agenda.window_end
            != datetime.fromisoformat(admission["valid_until"])
            + timedelta(seconds=perpetual_horizon_seconds(admission["context"]["horizon"]))
            or not datetime.fromisoformat(admission["close_window"]["as_of"])
            <= agenda.as_of
            <= datetime.fromisoformat(admission["admitted_at"])
        ):
            raise ValueError("perpetual_research_agenda_binding_changed")
    contract = source.read_contract_in_session(session, values.instrument)
    profile = read_execution_profile(partition.instrument_id, values.execution_profile)
    if values.account_snapshot.account_kind == "paper":
        from ..persistence.perpetual_paper_repository import PerpetualPaperRepository

        repository = PerpetualPaperRepository(session)
        state = (
            repository.current(admission["owner_id"], partition.account_id)
            if purpose == "active_research"
            else repository.revision(values.account_snapshot.original.ref)
            if values.account_snapshot.original is not None
            else None
        )
        if (
            state is None
            or values.account_snapshot.original is None
            or state["revision"] != values.account_snapshot.original.model_dump(mode="json")
            or state["owner_id"] != admission["owner_id"]
            or state["account_id"] != partition.account_id
        ):
            raise ValueError("perpetual_research_account_original_changed")
        for inst, reference in state["instrument_refs"].items():
            other = source.read_contract_in_session(
                session, DecisionSourceRefV1.model_validate(reference)
            )
            if inst != "okx:" + other.inst_id:
                raise ValueError("perpetual_research_account_instrument_changed")
    window = read_perpetual_close_window_in_session(
        session,
        instrument_ref=values.instrument,
        capture_ids=tuple(admission["close_window"]["capture_ids"]),
        as_of=datetime.fromisoformat(admission["close_window"]["as_of"]),
        archive=source.archive,
        instrument_reader=source.read_contract_in_session,
    )
    method, method_ref, method_hash = (
        comparison_method_original(include_source="source_bundle" in admission["comparison_method"])
        if purpose == "active_research"
        else _frozen_comparison_method(admission)
    )
    from .perpetual_cost_assumptions import read_perpetual_cost_assumptions

    costs = read_perpetual_cost_assumptions(
        DecisionSourceRefV1.model_validate(admission["cost_assumptions_ref"])
    )
    if (
        costs.body != admission["cost_assumptions"]
        or costs.base_cost_policy.model_dump(mode="json") != admission["cost_policy"]
    ):
        raise ValueError("perpetual_research_cost_assumption_original_changed")
    if (
        _window_json(window) != admission["close_window"]
        or method != admission["comparison_method"]
        or method_ref != admission["method_ref"]
        or method_hash != admission["method_code_hash"]
    ):
        raise ValueError("perpetual_research_source_or_method_changed")
    if (
        contract.model_dump(mode="json") != admission["instrument_contract"]
        or profile.model_dump(mode="json") != admission["execution_profile"]
    ):
        raise ValueError("perpetual_research_contract_or_profile_changed")
    if "direction_base_rate" in admission:
        from .perpetual_base_rate_source import read_direction_base_rate_in_session

        frozen = admission["direction_base_rate"]
        threshold = next(
            row["fraction"]
            for row in admission["close_window"]["thresholds"]
            if row["horizon"] == admission["context"]["horizon"]
        )
        if (
            frozen.get("threshold_fraction") != threshold
            or frozen.get("horizon_seconds")
            != perpetual_horizon_seconds(admission["context"]["horizon"])
            or read_direction_base_rate_in_session(
                session,
                source.archive,
                inst_id=partition.instrument_id.removeprefix("okx:"),
                capture_ids=tuple(frozen["capture_ids"]),
                as_of=datetime.fromisoformat(frozen["as_of"].replace("Z", "+00:00")),
                threshold_fraction=threshold,
                horizon_seconds=frozen["horizon_seconds"],
            )
            != frozen
        ):
            raise ValueError("perpetual_research_base_rate_original_changed")
    return admission


class PerpetualResearchBindingService:
    def __init__(
        self,
        database: Database,
        source: OKXPerpetualSource,
        *,
        now: Callable[[], datetime] = utcnow,
    ):
        if source.database is not database:
            raise ValueError("perpetual_research_database_mismatch")
        self.database, self.source, self.now = database, source, now

    def freeze_admission(self, *, run_id, owner_id, partition, capture_ids, as_of, valid_until):
        partition = DecisionSettingsPartitionV1.model_validate(partition)
        with self.database.session() as session:
            if _row(session, run_id, ADMISSION_ROLE) is not None:
                original, ref = _read(session, run_id, ADMISSION_ROLE)
                _authenticate_admission(session, original)
                if (
                    original["owner_id"] != owner_id
                    or original["partition"] != partition.model_dump(mode="json")
                    or original["close_window"]["capture_ids"] != list(capture_ids)
                    or datetime.fromisoformat(original["close_window"]["as_of"]) != aware_utc(as_of)
                    or datetime.fromisoformat(original["valid_until"]) != aware_utc(valid_until)
                ):
                    raise ValueError("perpetual_research_admission_conflict")
                return DecisionSourceRefV1.model_validate(ref)
            current = _settings_original(session, owner_id, partition)
            instrument = current.revision.values.instrument
        window = PerpetualCloseSource(self.database, self.source).prepare_perpetual_close_window(
            instrument_ref=instrument, capture_ids=capture_ids, as_of=as_of
        )
        at, until = aware_utc(self.now()), aware_utc(valid_until)
        if (
            not aware_utc(as_of) <= at < until
            or not 0 <= (at - window.value.end_at).total_seconds() <= 120
        ):
            raise ValueError("perpetual_research_window_stale_or_clock_invalid")

        def read(session):
            run, study = _run(session, run_id)
            if partition.instrument_id != "okx:" + study.instrument:
                raise ValueError("perpetual_research_instrument_scope_mismatch")
            if (
                run.status not in {"admitted", "dispatched", "running"}
                or run.finished_at is not None
            ):
                raise ValueError("perpetual_research_run_inactive")
            if session.get(DshSessionLinkRecord, run_id) or _row(
                session, run_id, "research_task_request"
            ):
                raise ValueError("perpetual_research_admission_after_task")
            saved = _settings_original(session, owner_id, partition)
            if saved.revision != current.revision:
                raise ValueError("perpetual_research_settings_head_changed")
            return self.prepare_admission_body_in_session(
                session,
                run_id=run_id,
                event_id=run.event_id,
                study_id=study.study_id,
                inst_id=study.instrument,
                owner_id=owner_id,
                partition=partition,
                saved=saved,
                window=window,
                at=at,
                until=until,
                horizon=study.horizon_scope,
            )

        prepared = prepare_originals(self.database, read)
        with self.database.session() as session:
            begin_immediate(session)
            window.verify(session)
            prepared.verify(session)
            from ..persistence.action_repository import ActionRepository

            ActionRepository(session).register_opportunity(
                DecisionOpportunityV2.model_validate(prepared.value["opportunity"])
            )
            ref = _save(session, run_id, ADMISSION_ROLE, prepared.value)
            session.flush()
            return ref

    def prepare_admission_body_in_session(
        self,
        session,
        *,
        run_id,
        event_id,
        study_id,
        inst_id,
        owner_id,
        partition,
        saved,
        window,
        at,
        until,
        horizon="4h",
        input_authority=None,
    ):
        """Build source-authenticated inputs outside a writer; IDs can be a server plan."""
        partition = DecisionSettingsPartitionV1.model_validate(partition.model_dump(mode="json"))
        horizon = require_perpetual_horizon(horizon)
        duration = perpetual_horizon_seconds(horizon)
        values = saved.revision.values
        from .perpetual_paper import PerpetualPaperService

        paper = PerpetualPaperService(self.database, self.source, now=lambda: at)
        account_inputs = None
        deadline_policy = None
        if input_authority is not None:
            from .perpetual_research_inputs import prepare_effective_inputs_in_session

            account_inputs = prepare_effective_inputs_in_session(
                session, authority=input_authority, saved=saved, paper_projection=paper
            )
            if (
                account_inputs.binding.run_id != run_id
                or account_inputs.binding.owner_id != owner_id
                or account_inputs.binding.partition != partition
                or account_inputs.binding.horizon != horizon
                or account_inputs.binding.prepared_at != aware_utc(at)
                or account_inputs.binding.valid_until != aware_utc(until)
            ):
                raise ValueError("perpetual_research_input_authority_scope_mismatch")
            values = account_inputs.effective_values
            deadline_policy = input_authority.deadline_policy
        DecisionSettingsBindingReader(
            self.source, now=lambda: at, paper_account_reader=paper.account_reader
        )(
            session,
            owner_id,
            DecisionSettingsCommandV1(
                schema_version="decision-settings-command.v1",
                request_id="research-original-check",
                expected_state_version=saved.state_version,
                operation="upsert",
                partition=partition,
                values=values,
            ),
        )
        contract = self.source.read_contract_in_session(session, values.instrument)
        profile = read_execution_profile(partition.instrument_id, values.execution_profile)
        settings_ref = {"ref": saved.revision.revision_id, "hash": saved.revision.payload_hash}
        context = DecisionActionContextV2.model_validate(
            dict(
                owner_id=owner_id,
                account_kind=partition.account_kind,
                account_id=partition.account_id,
                instrument_id=partition.instrument_id,
                scope_id=f"crypto:perp:okx:{inst_id}:trade:1m",
                profile_ref=values.execution_profile,
                horizon=horizon,
                instrument_ref=values.instrument,
                settings_ref=settings_ref,
                account_ref=(
                    account_inputs.binding.effective_refs.account_ref
                    if account_inputs is not None
                    else _ref(
                        saved.revision.revision_id + "#account",
                        values.account_snapshot.model_dump(mode="json"),
                    )
                ),
                position_ref=(
                    account_inputs.binding.effective_refs.position_ref
                    if account_inputs is not None
                    else _ref(
                        saved.revision.revision_id + "#position",
                        values.position_context.model_dump(mode="json"),
                    )
                ),
                utility_ref={
                    "ref": values.utility_policy.policy_ref,
                    "hash": values.utility_policy.hash,
                },
            )
        )
        method, method_ref, method_hash = comparison_method_original()
        from .perpetual_cost_assumptions import perpetual_cost_assumptions_original

        costs = perpetual_cost_assumptions_original()
        threshold = next(item for item in window.value.thresholds if item.horizon == horizon)
        clock = dict(
            schema_version="perpetual-publication-clock-policy.v1",
            anchor="publication_bound",
            clock_rule="strict_next_utc_minute",
            grid_seconds=60,
            price_basis="trade_1m_close",
        )
        family = dict(
            schema_version="perpetual-target-family.v1",
            kind="terminal_market_direction",
            instrument_id=partition.instrument_id,
            horizon=horizon,
            duration_seconds=duration,
        )
        label = dict(
            schema_version="perpetual-label-rule.v1",
            basis="terminal_close_return",
            up="return_gt_threshold",
            down="return_lt_negative_threshold",
            neutral="inclusive_between_thresholds",
        )
        protocol = dict(
            schema_version="perpetual-target-protocol.v1",
            qualification="research_trial",
            source_window_ref=window.value.source_ref.model_dump(mode="json"),
            threshold_method_ref=window.value.method_ref.model_dump(mode="json"),
        )
        policies = {
            key: {"body": body, "ref": _ref("decision-policy:" + key + ".v1", body)}
            for key, body in dict(
                clock=clock, target_family=family, label=label, protocol=protocol
            ).items()
        }
        invalidation = dict(
            schema_version="perpetual-input-invalidation-policy.v1",
            settings_ref=settings_ref,
            instrument_ref=values.instrument.model_dump(mode="json"),
            window_ref=window.value.source_ref.model_dump(mode="json"),
            method_ref=method_ref,
            conditions=[
                "settings_head_changed",
                "account_original_changed",
                "instrument_original_changed",
                "threshold_window_original_changed",
                "method_code_changed",
                "clock_expired",
            ],
        )
        invalidation_ref = _ref("decision-policy:perpetual-input-invalidation.v1", invalidation)
        target = PendingPublicationTargetV1.model_validate(
            dict(
                schema_version="pending-publication-target.v1",
                target_id="perp-target:" + canonical_hash([run_id, horizon]),
                instrument_ref=values.instrument,
                horizon=horizon,
                duration_seconds=duration,
                clock_policy_ref=policies["clock"]["ref"],
                price_basis="trade_1m_close",
                threshold_fraction=threshold.fraction,
                method_ref=window.value.method_ref,
                target_family_ref=policies["target_family"]["ref"],
                label_rule_ref=policies["label"]["ref"],
                protocol_ref=policies["protocol"]["ref"],
                conditioning_ref=None,
                anchor="publication_bound",
                grid_seconds=60,
                clock_rule="strict_next_utc_minute",
                valid_until=until,
                invalidation_refs=[invalidation_ref],
            )
        )
        target_body = target.model_dump(mode="json")
        target_ref = _ref("perp-pending-target:" + canonical_hash(run_id), target_body)
        rule = PublicationAnchorRuleV1.model_validate(
            dict(
                schema_version="publication-anchor-rule.v1",
                candidate_target_ref=target_ref,
                **{
                    key: target_body[key]
                    for key in (
                        "anchor",
                        "horizon",
                        "duration_seconds",
                        "grid_seconds",
                        "clock_rule",
                        "price_basis",
                        "threshold_fraction",
                        "method_ref",
                        "clock_policy_ref",
                        "valid_until",
                        "invalidation_refs",
                    )
                },
            )
        )
        rule_body = rule.model_dump(mode="json")
        policy = dict(
            schema_version="perpetual-action-domain-policy.v1",
            method_ref=method_ref,
            method_code_hash=method_hash,
            variables=[],
            invalidation_refs=[invalidation_ref],
            valid_until=target_body["valid_until"],
            mutation_scope="none_original_frozen_inputs_only",
            deterministic_recomparison_enabled=False,
        )
        if deadline_policy is not None:
            policy["deadline_policy"] = deadline_policy.model_dump(mode="json")
        opportunity = DecisionOpportunityV2.model_validate(
            dict(
                schema_version="decision-opportunity.v2",
                id="perpop_" + canonical_hash([run_id, horizon]),
                key="perpetual-research:" + run_id + ":" + horizon,
                scope_id=context.scope_id,
                horizon=horizon,
                kind="event",
                event_ref=event_id,
                registered_at=at,
                time_start=at,
                time_end=until,
                target_spec_hash=target_ref["hash"],
                recipe_hash=canonical_hash(
                    {
                        "method": method_ref,
                        "threshold_method": window.value.method_ref.model_dump(mode="json"),
                    }
                ),
                cohort="prospective",
                status="open",
                reason=None,
            )
        )
        from .perpetual_event_agenda import collect_event_agenda_in_session

        agenda = collect_event_agenda_in_session(
            session,
            archive=self.source.archive,
            as_of=at,
            instrument_refs=[values.instrument],
            window_end=until + timedelta(seconds=duration),
        )
        admission = dict(
            schema_version="perpetual-research-admission.v1",
            run_id=run_id,
            event_id=event_id,
            study_id=study_id,
            owner_id=owner_id,
            partition=partition.model_dump(mode="json"),
            settings_revision=saved.revision.model_dump(mode="json"),
            settings_state_version=saved.state_version,
            context=context.model_dump(mode="json"),
            opportunity_id=opportunity.id,
            opportunity=opportunity.model_dump(mode="json"),
            close_window=_window_json(window.value),
            event_agenda=agenda.model_dump(mode="json"),
            instrument_contract=contract.model_dump(mode="json"),
            execution_profile=profile.model_dump(mode="json"),
            candidate_target=target_body,
            candidate_target_ref=target_ref,
            anchor_rule=rule_body,
            anchor_rule_ref=_ref("perp-anchor-rule:" + canonical_hash(run_id), rule_body),
            comparison_method=method,
            method_ref=method_ref,
            method_code_hash=method_hash,
            allowed_domain_policy=policy,
            cost_assumptions=costs.body,
            cost_assumptions_ref=costs.reference.model_dump(mode="json"),
            cost_policy=costs.base_cost_policy.model_dump(mode="json"),
            cost_policy_ref=costs.base_cost_ref.model_dump(mode="json"),
            allowed_domain_policy_ref=_ref("perp-domain-policy:" + canonical_hash(run_id), policy),
            invalidation_policy=invalidation,
            invalidation_policy_ref=invalidation_ref,
            policies=policies,
            archive_root=str(self.source.archive.root.resolve()),
            recorded_at=_json_value(at),
            admitted_at=_json_value(at),
            valid_until=target_body["valid_until"],
            qualification="research_trial",
            product_release_qualified=False,
        )
        if account_inputs is not None:
            from ..ports.perpetual_research_inputs import PreparedPerpetualAdmissionV2

            admission.update(
                schema_version="perpetual-research-admission.v2",
                input_policy=account_inputs.binding.input_policy,
                input_binding_ref=account_inputs.binding_ref.model_dump(mode="json"),
                effective_values=values.model_dump(mode="json"),
            )
            if deadline_policy is not None:
                from .perpetual_research_horizon import perpetual_deadline_policy_original

                admission.update(
                    schema_version="perpetual-research-admission.v3",
                    deadline_prepared_at=_json_value(at),
                    deadline_policy_ref=perpetual_deadline_policy_original()[1],
                )
            return PreparedPerpetualAdmissionV2(
                account_binding=account_inputs.binding, admission=admission
            ).model_dump(mode="json")
        return admission

    def read_admission_in_session(self, session, run_id):
        return _authenticate_admission(session, _read(session, run_id, ADMISSION_ROLE)[0])

    def prepare_for_factory(self, event_id, run_id):
        with self.database.session() as session:
            if _row(session, run_id, ADMISSION_ROLE) is None:
                return None
        return prepare_originals(
            self.database, lambda session: self._factory_read(session, event_id, run_id)
        )

    def _factory_read(self, session, event_id, run_id):
        _run(session, run_id, event_id=event_id)
        return self.read_admission_in_session(session, run_id)

    def bind_task_in_session(self, session, request, prepared):
        # Factory already verified the full readset before its own snapshot/Task
        # writes in this continuous BEGIN IMMEDIATE. Those intentional Run edits
        # must not be mistaken for a concurrent reader substitution afterward.
        prepared._require_issued(session)
        if not prepared.source.matches():
            raise ValueError("perpetual_research_source_changed")
        admission = prepared.value
        if (
            request.run_id != admission["run_id"]
            or request.event_id != admission["event_id"]
            or request.output_schema_ref not in OUTPUT_SCHEMAS
            or request.research_mode != "current_decision"
            or request.deadline_at
            > datetime.fromisoformat(admission.get("runtime_deadline_at", admission["valid_until"]))
        ):
            raise ValueError("perpetual_research_task_scope_mismatch")
        original_admission, admission_ref = _read(session, request.run_id, ADMISSION_ROLE)
        if original_admission != admission:
            raise ValueError("perpetual_research_admission_changed")
        from .research_task import read_task_request

        original = read_task_request(session, session.get(RunRecord, request.run_id))
        if original is None or original[0] != request:
            raise ValueError("perpetual_research_original_task_missing")
        return _save(
            session,
            request.run_id,
            TASK_BINDING_ROLE,
            dict(
                schema_version="perpetual-research-task-binding.v1",
                admission_ref=admission_ref,
                run_id=request.run_id,
                event_id=request.event_id,
                request_id=request.request_id,
                original_task_hash=canonical_hash(request.model_dump(mode="json")),
                output_schema_ref=request.output_schema_ref,
                recorded_at=_json_value(self.now()),
            ),
        )


def _selection_reference_from_row(row):
    from .research_context_dispatch import parse_context_selection_reference

    identity = row.model_dump(mode="json")
    identity["schema_version"] = identity.pop("row_schema_version")
    return parse_context_selection_reference(canonical(identity))


def _authenticate_selected_request_in_session(
    session: Session,
    request: ResearchSessionRequest,
    selection_reference,
    expected_lease_owner: str | None,
):
    from .research_context_dispatch import parse_context_selection_reference
    from .research_context_execution import (
        assert_selection_permission_in_session,
        read_selection_in_session,
    )

    reference = parse_context_selection_reference(
        selection_reference.model_dump_json()
        if hasattr(selection_reference, "model_dump_json")
        else canonical(selection_reference)
    )
    selected = (
        assert_selection_permission_in_session(
            session,
            request.run_id,
            reference,
            expected_lease_owner,
        )
        if expected_lease_owner is not None
        else read_selection_in_session(session, request.run_id, reference)
    )
    if canonical(selected.request.model_dump(mode="json")) != canonical(
        request.model_dump(mode="json")
    ):
        raise ValueError("perpetual_research_request_identity_mismatch")
    return selected


def read_perpetual_research_binding_in_session(
    session: Session,
    request_or_run_id,
    *,
    selection_reference=None,
    expected_lease_owner: str | None = None,
    purpose="active_research",
):
    request = (
        None
        if isinstance(request_or_run_id, str)
        else ResearchSessionRequest.model_validate(
            request_or_run_id.model_dump(mode="json")
            if hasattr(request_or_run_id, "model_dump")
            else request_or_run_id
        )
    )
    run_id = request.run_id if request is not None else request_or_run_id
    admission, admission_ref = _read(session, run_id, ADMISSION_ROLE)
    _authenticate_admission(session, admission, purpose=purpose)
    binding, binding_ref = _read(session, run_id, TASK_BINDING_ROLE)
    from .research_task import read_task_request

    original = read_task_request(session, session.get(RunRecord, run_id))
    if original is None or binding["original_task_hash"] != canonical_hash(
        original[0].model_dump(mode="json")
    ):
        raise ValueError("perpetual_research_task_original_changed")
    if (
        binding["admission_ref"] != admission_ref
        or original[0].output_schema_ref not in OUTPUT_SCHEMAS
    ):
        raise ValueError("perpetual_research_task_admission_mismatch")
    if request is not None and (
        request.run_id != binding["run_id"]
        or request.event_id != binding["event_id"]
        or request.output_schema_ref not in OUTPUT_SCHEMAS
    ):
        raise ValueError("perpetual_research_request_identity_mismatch")
    if request is not None and selection_reference is not None:
        _authenticate_selected_request_in_session(
            session,
            request,
            selection_reference,
            expected_lease_owner,
        )
    elif request is not None:
        from .research_request_identity import research_request_hash

        if research_request_hash(request) != research_request_hash(original[0]):
            raise ValueError("perpetual_research_request_identity_mismatch")
    from .perpetual_research_inputs import read_effective_inputs_in_session

    resolved = read_effective_inputs_in_session(session, admission, purpose=purpose)
    values = resolved.effective_values.model_dump(mode="json")
    materials = {
        key: admission[key]
        for key in (
            "context",
            "opportunity_id",
            "instrument_contract",
            "execution_profile",
            "close_window",
            "candidate_target",
            "candidate_target_ref",
            "anchor_rule",
            "anchor_rule_ref",
            "comparison_method",
            "method_ref",
            "method_code_hash",
            "allowed_domain_policy",
            "allowed_domain_policy_ref",
            "invalidation_policy",
            "invalidation_policy_ref",
            "policies",
            "cost_assumptions",
            "cost_assumptions_ref",
            "cost_policy",
            "cost_policy_ref",
            "qualification",
            "product_release_qualified",
        )
    }
    materials.update(
        admission_ref=admission_ref,
        task_binding_ref=binding_ref,
        original_task_hash=binding["original_task_hash"],
        settings_revision=admission["settings_revision"],
        **{key: values[key] for key in ("account_snapshot", "position_context", "utility_policy")},
        economic_input_originals=[
            {
                "role": "explicit_research_cost_assumption",
                "ref": admission["cost_assumptions_ref"],
                "body": admission["cost_assumptions"],
            }
        ],
        economic_input_availability="partial",
        economic_input_reason="subjective_complete_paths_required_from_research_actual_fee_tier_and_future_funding_unknown",
    )
    materials["close_window"] = _native_window(admission["close_window"])
    if resolved.binding is not None:
        materials.update(
            input_policy=resolved.binding.input_policy,
            input_binding_ref=admission["input_binding_ref"],
            input_binding=resolved.binding.model_dump(mode="json"),
        )
    automatic = admission.get("automatic_source")
    if automatic and automatic["source"]["schema_version"] == "perpetual-plan-review-trigger.v1":
        materials["policies"] = {
            **materials["policies"],
            "plan_review_motive": {
                "source": automatic,
                "parent_admission_ref": admission_ref,
                "child_path": "/automatic_source",
                "purpose": "historical_research_motive_only",
                "fresh_market_evidence": False,
                "execution_authorized": False,
            },
        }

    if "direction_base_rate" in admission:
        materials.update(
            direction_base_rate=admission["direction_base_rate"],
            direction_base_rate_ref=admission_ref,
            direction_base_rate_path="/direction_base_rate",
        )
    if "event_agenda" in admission:
        materials.update(
            event_agenda=admission["event_agenda"],
            event_agenda_ref=admission_ref,
            event_agenda_path="/event_agenda",
        )
    return dict(
        admission=admission,
        admission_ref=admission_ref,
        task_binding=binding,
        binding_ref=binding_ref,
        materials=materials,
    )


def read_perpetual_research_materials_in_session(
    session: Session,
    request: ResearchSessionRequest,
    *,
    selection_reference=None,
    expected_lease_owner: str | None = None,
):
    from .perpetual_review_materials import read_review_originals_in_session

    saved = read_perpetual_research_binding_in_session(
        session,
        request,
        selection_reference=selection_reference,
        expected_lease_owner=expected_lease_owner,
    )
    materials = saved["materials"]
    as_of = datetime.fromisoformat(saved["admission"]["admitted_at"])
    if selection_reference is not None:
        selected = _authenticate_selected_request_in_session(
            session,
            request,
            selection_reference,
            expected_lease_owner,
        )
        as_of = selected.receipt.effective_eligibility_cutoff_at
    return dict(
        materials,
        review_originals=read_review_originals_in_session(
            session,
            request,
            materials,
            as_of=as_of,
        ),
    )


def read_authenticated_perpetual_execution_in_session(
    session: Session,
    run_id: str,
    generation: int,
    *,
    expected_lease_owner: str | None = None,
    purpose="active_research",
):
    """Return one prepared request only after its exact Selection and Task re-open."""
    from .research_execution_binding import read_prepared_execution_in_session

    prepared = read_prepared_execution_in_session(session, run_id, generation)
    request = ResearchSessionRequest.model_validate(
        prepared.execution.request.model_dump(mode="json")
    )
    selection_reference = (
        _selection_reference_from_row(prepared.execution.current_selection_row)
        if isinstance(prepared.execution, ResearchExecutionRequestReceiptV3)
        else None
    )
    frozen = read_perpetual_research_binding_in_session(
        session,
        request,
        selection_reference=selection_reference,
        expected_lease_owner=(expected_lease_owner if selection_reference is not None else None),
        purpose=purpose,
    )
    return request, frozen, prepared


def read_perpetual_admission_identity_in_session(session: Session, run_id: str):
    """SQL identity only for source/scope selection; prepared readers authenticate raw inputs."""
    if _row(session, run_id, ADMISSION_ROLE) is None:
        return None
    admission, reference = _read(session, run_id, ADMISSION_ROLE)
    run, study = _run(session, run_id, event_id=admission["event_id"])
    _require_admission_horizon(admission, study)
    if (
        admission["study_id"] != study.study_id
        or admission["context"]["instrument_id"] != "okx:" + study.instrument
        or admission["context"]["horizon"] != study.horizon_scope
    ):
        raise ValueError("perpetual_research_admission_identity_mismatch")
    return admission, reference


def read_native_perpetual_binding(
    database: Database,
    run_id: str,
    request_hash: str,
    generation: int,
    *,
    source_call_id: str | None = None,
    now=utcnow,
):
    """Service-only current execution view, never a latest-Run financial selector."""
    from .research_request_identity import research_request_hash

    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        at = aware_utc(now())
        run, study = _run(session, run_id)
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        if (
            link is None
            or link.request_hash != request_hash
            or link.generation != generation
            or link.terminal_at is not None
            or link.state in {"completed", "failed", "cancelled"}
            or link.deadline_at is None
            or as_utc(link.deadline_at) <= at
            or run.status != "running"
            or run.finished_at is not None
            or not run.lease_owner
            or run.lease_expires_at is None
            or as_utc(run.lease_expires_at) <= at
        ):
            raise ValueError("perpetual_research_current_execution_mismatch")
        request, frozen, execution = read_authenticated_perpetual_execution_in_session(
            session,
            run_id,
            generation,
            expected_lease_owner=run.lease_owner,
        )
        if research_request_hash(request) != request_hash:
            raise ValueError("perpetual_research_execution_request_mismatch")
        admission = frozen["admission"]
        if at >= datetime.fromisoformat(admission["valid_until"]):
            raise ValueError("perpetual_research_admission_expired")
        cutoff = datetime.fromisoformat(admission["admitted_at"])
        if isinstance(execution.execution, ResearchExecutionRequestReceiptV3):
            selected = _authenticate_selected_request_in_session(
                session,
                request,
                _selection_reference_from_row(execution.execution.current_selection_row),
                run.lease_owner,
            )
            cutoff = selected.receipt.effective_eligibility_cutoff_at
        event_draft = None
        event_source = None
        event_source_context = None
        if source_call_id is not None:
            from .provider_input_receipts import read_provider_emission_in_session, strict_json

            original = read_provider_emission_in_session(
                session,
                run_id=run_id,
                generation=generation,
                root_session_id=link.dsh_session_id,
                execution_session_id=link.dsh_session_id,
                call_id=source_call_id,
            )
            if original is None:
                raise ValueError("perpetual_research_snapshot_emission_missing")
            emission, _reference = original
            args = strict_json(emission.submit.raw_arguments_json)
            if (
                emission.submit.tool_name != "decision_hub_synthesis_submit"
                or emission.submit.request_hash != request_hash
                or emission.known_at > at
                or not isinstance(args, dict)
            ):
                raise ValueError("perpetual_research_snapshot_emission_invalid")
            if args.get("action") == "prepare":
                from packages.contracts_py.decision_hub_contracts.generated import (
                    decision_product_v2 as contracts,
                )

                event_draft = (
                    args.get("draft")
                    if args.get("draft", {}).get("schema_version") == "perpetual-synthesis-draft.v2"
                    else None
                )
                parsed = (
                    contracts.PerpetualPrepareArgumentsV2
                    if event_draft is not None
                    else contracts.PerpetualPrepareArgumentsV1
                ).model_validate(args)
                if parsed.request_id != request.request_id:
                    raise ValueError("perpetual_research_snapshot_request_mismatch")
                if event_draft is not None:
                    event_source = (emission, _reference)
                    event_source_context = dict(
                        run_id=run_id,
                        generation=generation,
                        root_session_id=link.dsh_session_id,
                        execution_session_id=link.dsh_session_id,
                        call_id=source_call_id,
                    )
            elif (
                set(args) != {"candidate", "review_request"}
                or not isinstance(args["candidate"], dict)
                or args["candidate"].get("schema_version") != "research-decision-synthesis.v2"
                or args["candidate"].get("request_id") != request.request_id
            ):
                raise ValueError("perpetual_research_snapshot_prepare_required")
            # A Provider block is durable before the SDK can emit its native
            # Tool call. This conservative snapshot never uses HTTP wall time.
            cutoff = emission.known_at.replace(
                microsecond=emission.known_at.microsecond // 1000 * 1000
            )
        from .perpetual_review_materials import read_review_originals_in_session

        catalog = read_review_originals_in_session(
            session, request, frozen["materials"], as_of=cutoff
        )
        if event_draft is not None:
            from ..ports.event_scenario_preparation import prepare_event_scenario_catalog

            catalog = prepare_event_scenario_catalog(
                event_draft,
                binding={
                    key: admission[key]
                    for key in (
                        "opportunity_id",
                        "context",
                        "candidate_target_ref",
                        "anchor_rule_ref",
                        "method_ref",
                        "method_code_hash",
                    )
                },
                catalog=catalog,
                source_known_at=event_source[0].known_at.isoformat().replace("+00:00", "Z"),
                source_emission_ref=event_source[1].model_dump(mode="json"),
                source_emission_context=event_source_context,
            )

        return dict(
            schema_version="decision-v2-native-binding.v1",
            run_id=run_id,
            request_hash=request_hash,
            generation=generation,
            binding_ref=frozen["binding_ref"],
            review_originals=catalog,
            **{
                key: admission[key]
                for key in (
                    "opportunity_id",
                    "context",
                    "candidate_target_ref",
                    "anchor_rule_ref",
                    "method_ref",
                    "method_code_hash",
                )
            },
        )
