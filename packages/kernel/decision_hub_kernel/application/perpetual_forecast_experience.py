"""Mature direction labels and bounded historical Task input, never trade labels.

The unique publisher's existing immutable originals select the forecast. Archive
authentication and arithmetic precede writers; existing Run originals store the
experience. Delivery to a Task is not proof of ProviderInput or skill consumption.
"""

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.action_quote_repository import _capture
from ..persistence.action_repository import ActionRepository
from ..persistence.db import RawCaptureRecord, RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_close import millis, parse_page
from ..ports.perpetual_forecast_experience import (
    METHOD,
    ROLE,
    SCHEMA,
    ForecastExperienceOriginal,
    classify_forecast_return,
    experience_original,
    score_direction,
)
from .market_original_parse import MarketOriginalParse
from .okx_perpetual_source import OKXPerpetualOriginalReader
from .perpetual_advice_query import PUBLISHER, PerpetualAdviceQuery, _original
from .perpetual_research_binding import canonical, read_perpetual_admission_identity_in_session


def _time(value):
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _software():
    root = Path(__file__).resolve().parents[1]
    paths = (
        "application/perpetual_forecast_experience.py",
        "ports/perpetual_forecast_experience.py",
        "ports/perpetual_close.py",
        "application/market_original_parse.py",
    )
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}


def _publication(session, run_id, cutoff):
    identity = read_perpetual_admission_identity_in_session(session, run_id)
    if identity is None:
        raise ValueError("perpetual_experience_admission_missing")
    admission, admission_ref = identity
    if admission["context"]["horizon"] != "4h":
        raise ValueError("perpetual_experience_only_4h_supported")
    run = session.get(RunRecord, run_id, populate_existing=True)
    repo = ActionRepository(session)
    command = repo.get_command(
        PUBLISHER, "publish_" + canonical_hash([run_id, admission["opportunity_id"]])
    )
    if command is None:
        raise ValueError("perpetual_experience_published_forecast_required")
    # This reader authenticates the committed immutable closure without asking
    # today's settings/head. Expired advice remains a legitimate forecast label.
    result = PerpetualAdviceQuery._published(
        session,
        repo,
        run,
        admission,
        admission_ref,
        command,
        {
            "settings_revision": {
                "ref": admission["settings_revision"]["revision_id"],
                "hash": admission["settings_revision"]["payload_hash"],
            }
        },
        cutoff,
    )
    bindings = result["snapshot"]["bindings"]
    derivation = _original(
        session, run_id, bindings["derivation"], "perpetual-publication-derivation.v1"
    )
    materials = _original(
        session, run_id, derivation["research_materials_ref"], "perpetual-research-materials.v1"
    )
    target = result["target"]
    expected_label = dict(
        schema_version="perpetual-label-rule.v1",
        basis="terminal_close_return",
        up="return_gt_threshold",
        down="return_lt_negative_threshold",
        neutral="inclusive_between_thresholds",
    )
    label = materials["policies"]["label"]
    family = materials["policies"]["target_family"]
    if (
        label["body"] != expected_label
        or canonical_hash(label["body"]) != label["ref"]["hash"]
        or target["label_rule_ref"] != label["ref"]
        or target["target_family_ref"] != family["ref"]
        or canonical_hash(family["body"]) != family["ref"]["hash"]
        or family["body"]
        != dict(
            schema_version="perpetual-target-family.v1",
            kind="terminal_market_direction",
            instrument_id=admission["partition"]["instrument_id"],
            horizon="4h",
            duration_seconds=14400,
        )
        or target["price_basis"] != "trade_1m_close"
        or target["duration_seconds"] != 14400
        or target["conditioning_ref"] is not None
    ):
        raise ValueError("perpetual_experience_target_policy_unsupported")
    frozen, market = result["frozen_forecast"], result["market_distribution"]
    estimate = market["direction_distribution"]
    if (
        market["instrument_ref"] != target["instrument_ref"]
        or market["market_target_ref"] != frozen["candidate_target_ref"]
        or estimate["target_ref"] != frozen["candidate_target_ref"]
        or estimate["conditioning_ref"] is not None
        or market["conditioning_ref"] is not None
        or datetime.fromisoformat(estimate["as_of"].replace("Z", "+00:00")) > command.accepted_at
    ):
        raise ValueError("perpetual_experience_direction_target_or_condition_unverified")
    return admission, result, command


def _derive(session, *, run_id, capture_ids, archive, instrument_reader, recorded_at):
    at = aware_utc(recorded_at)
    if (
        not isinstance(capture_ids, tuple)
        or not 1 <= len(capture_ids) <= 2
        or len(set(capture_ids)) != len(capture_ids)
    ):
        raise ValueError("perpetual_experience_capture_list_invalid")
    admission, publication, command = _publication(session, run_id, at)
    target = publication["target"]
    start, end = (
        datetime.fromisoformat(target[key].replace("Z", "+00:00"))
        for key in ("window_start", "window_end")
    )
    if end - start != timedelta(hours=4) or start.second or start.microsecond or end > at:
        raise ValueError("perpetual_experience_target_not_mature")
    contract = instrument_reader(
        session, DecisionSourceRefV1.model_validate(target["instrument_ref"])
    )
    if (
        "okx:" + contract.inst_id != admission["partition"]["instrument_id"]
        or contract.instrument_kind != "perpetual"
    ):
        raise ValueError("perpetual_experience_instrument_mismatch")
    pages = []
    parser = MarketOriginalParse()
    for capture_id in capture_ids:
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perpetual_experience_capture_missing")
        pages.append(
            parse_page(
                _capture(row),
                archive.read(row.body_ref),
                inst_id=contract.inst_id,
                as_of=at,
                selected_rows=parser.selected_rows,
            )
        )
    acquisition = pages[0].acquisition_id
    for index, page in enumerate(pages):
        if (
            page.acquisition_id != acquisition
            or page.kind != "base"
            or page.sequence != index
            or index == 0
            and page.after_ms != millis(end)
            or index > 0
            and page.after_ms != millis(pages[index - 1].closes[-1].open_at)
        ):
            raise ValueError("perpetual_experience_cursor_proof_invalid")
    closes = {}
    for page in pages:
        for close in page.closes:
            if not start <= close.close_at <= end:
                continue
            prior = closes.get(close.close_at)
            if prior is not None and prior.price != close.price:
                raise ValueError("perpetual_experience_conflicting_minute")
            closes.setdefault(close.close_at, close)
    expected = tuple(start + timedelta(minutes=index) for index in range(241))
    if set(closes) != set(expected):
        raise ValueError("perpetual_experience_exact_minute_missing")
    ordered = tuple(closes[stamp] for stamp in expected)
    known = max(command.accepted_at, *(close.recorded_at for close in ordered))
    if known > at or known < end:
        raise ValueError("perpetual_experience_label_clock_invalid")
    result, category = classify_forecast_return(
        ordered[0].price, ordered[-1].price, target["threshold_fraction"]
    )
    estimate = publication["market_distribution"]["direction_distribution"]
    score = score_direction(estimate["probabilities"], category)
    bindings = publication["snapshot"]["bindings"]
    body = dict(
        schema_version=SCHEMA,
        run_id=run_id,
        owner_id=admission["owner_id"],
        instrument_id=admission["partition"]["instrument_id"],
        horizon="4h",
        status="observed",
        snapshot_ref={
            "ref": publication["snapshot"]["id"],
            "hash": publication["snapshot"]["snapshot_hash"],
        },
        final_target_ref=bindings["final_target"],
        frozen_forecast_ref=bindings["frozen_forecast"],
        market_distribution_ref=publication["frozen_forecast"]["market_distribution_ref"],
        method_ref=target["method_ref"],
        target_family_ref=target["target_family_ref"],
        label_rule_ref=target["label_rule_ref"],
        target=target,
        direction_estimate=estimate,
        return_fraction=result,
        category=category,
        multiclass_brier=score,
        gaps=[] if score is not None else ["direction_probabilities_unavailable"],
        capture_ids=list(capture_ids),
        archive_root=str(archive.root.resolve()),
        closes=[
            {
                key: _time(value) if isinstance(value, datetime) else value
                for key, value in asdict(close).items()
            }
            for close in ordered
        ],
        known_at=_time(known),
        recorded_at=_time(at),
        calculation_method=METHOD,
        calculation_method_hash=canonical_hash(METHOD),
        software=_software(),
        qualification="research_trial",
        financial_qualified=False,
        skill_consumed=False,
        trade_outcome=False,
    )
    return experience_original(body)


def read_forecast_experience_in_session(session, reference, *, cutoff_at):
    ref = DecisionSourceRefV1.model_validate(reference)
    row = session.get(RunRoleOutputRecord, ref.ref, populate_existing=True)
    if row is None:
        raise ValueError("perpetual_experience_original_missing")
    body = json.loads(row.payload_json)
    at = datetime.fromisoformat(body["recorded_at"].replace("Z", "+00:00"))
    if (
        row.schema_version != SCHEMA
        or row.role != ROLE
        or row.run_id != body["run_id"]
        or row.payload_hash != ref.hash
        or canonical(body) != row.payload_json
        or experience_original(body).ref != ref
        or as_utc(row.created_at) != at
        or at > aware_utc(cutoff_at)
    ):
        raise ValueError("perpetual_experience_original_invalid_or_future")
    archive = RawBodyArchive(Path(body["archive_root"]))
    source = OKXPerpetualOriginalReader(archive)
    rebuilt = _derive(
        session,
        run_id=body["run_id"],
        capture_ids=tuple(body["capture_ids"]),
        archive=archive,
        instrument_reader=source.read_contract_in_session,
        recorded_at=at,
    )
    if rebuilt.body_json != row.payload_json:
        raise ValueError("perpetual_experience_original_changed")
    return rebuilt


class PerpetualForecastExperienceService:
    def __init__(self, database, instrument_source, *, clock=utcnow):
        if instrument_source.database is not database:
            raise ValueError("perpetual_experience_database_mismatch")
        self.database, self.source, self.clock = database, instrument_source, clock

    def prepare(self, *, run_id, capture_ids):
        at = aware_utc(self.clock())
        return prepare_originals(
            self.database,
            lambda session: _derive(
                session,
                run_id=run_id,
                capture_ids=capture_ids,
                archive=self.source.archive,
                instrument_reader=self.source.read_contract_in_session,
                recorded_at=at,
            ),
        )

    read_in_session = staticmethod(read_forecast_experience_in_session)

    def record(self, *, run_id, capture_ids) -> ForecastExperienceOriginal:
        with self.database.session() as session:
            prior = session.scalars(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == ROLE
                )
            ).one_or_none()
            if prior is not None:
                value = read_forecast_experience_in_session(
                    session,
                    {"ref": prior.output_id, "hash": prior.payload_hash},
                    cutoff_at=self.clock(),
                )
                if value.body["capture_ids"] != list(capture_ids):
                    raise ValueError("perpetual_experience_capture_conflict")
                return value
        prepared = self.prepare(run_id=run_id, capture_ids=capture_ids)
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            recorded_at = aware_utc(self.clock())
            if recorded_at < datetime.fromisoformat(
                prepared.value.body["recorded_at"].replace("Z", "+00:00")
            ):
                raise ValueError("perpetual_experience_record_clock_regressed")
            value = experience_original({**prepared.value.body, "recorded_at": _time(recorded_at)})
            prior = session.scalars(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == ROLE
                )
            ).one_or_none()
            if prior is not None:
                if prior.payload_json != value.body_json:
                    raise ValueError("perpetual_experience_concurrent_original_conflict")
            else:
                session.add(
                    RunRoleOutputRecord(
                        output_id=value.ref.ref,
                        run_id=run_id,
                        role=ROLE,
                        schema_version=SCHEMA,
                        payload_json=value.body_json,
                        payload_hash=value.ref.hash,
                        created_at=datetime.fromisoformat(
                            value.body["recorded_at"].replace("Z", "+00:00")
                        ),
                    )
                )
                session.flush()
            prepared.verify_after_writes(session)
        return value


def perpetual_experience_context_in_session(
    session, *, owner_id, instrument_id, horizon, cutoff_at, limit=3
):
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("perpetual_experience_context_limit_invalid")
    if horizon != "4h":
        return ()
    cutoff = aware_utc(cutoff_at)
    body = RunRoleOutputRecord.payload_json
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.role == ROLE,
            RunRoleOutputRecord.schema_version == SCHEMA,
            func.json_extract(body, "$.owner_id") == owner_id,
            func.json_extract(body, "$.instrument_id") == instrument_id,
            func.json_extract(body, "$.horizon") == horizon,
            RunRoleOutputRecord.created_at <= cutoff,
        )
        .order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id.desc())
        .limit(limit)
    ).all()
    inputs = []
    for row in rows:
        original = read_forecast_experience_in_session(
            session, {"ref": row.output_id, "hash": row.payload_hash}, cutoff_at=cutoff
        )
        value = original.body
        # Bounded, deterministic projection; no balance, trade result or resource instructions.
        summary = {
            key: value[key]
            for key in (
                "instrument_id",
                "horizon",
                "final_target_ref",
                "target_family_ref",
                "label_rule_ref",
                "method_ref",
                "category",
                "return_fraction",
                "multiclass_brier",
                "known_at",
                "recorded_at",
                "gaps",
            )
        }
        inputs.append(
            ResearchInputEvidence(
                evidence_id=original.ref.ref,
                kind="document",
                authority="unverified",
                source_id="perpetual_forecast_experience.v1",
                source_url=None,
                published_at=None,
                observed_at=datetime.fromisoformat(value["known_at"].replace("Z", "+00:00")),
                received_at=datetime.fromisoformat(value["recorded_at"].replace("Z", "+00:00")),
                content_hash=original.ref.hash,
                excerpt="历史四小时方向预测与实际结果；不是交易收益，不是技能指令，不授金融资格。"
                + canonical(summary),
            )
        )
    return tuple(inputs)


def prepare_perpetual_experience_inputs(database, admission):
    cutoff = datetime.fromisoformat(admission["admitted_at"])

    def read(session):
        from .perpetual_paper_experience import paper_experience_context_in_session

        partition = admission["partition"]
        paper = paper_experience_context_in_session(
            session,
            owner_id=admission["owner_id"],
            account_kind=partition["account_kind"],
            account_id=partition["account_id"],
            instrument_id=partition["instrument_id"],
            profile_ref=admission["context"]["profile_ref"],
            horizon=admission["context"]["horizon"],
            cutoff_at=cutoff,
            limit=2,
        )
        forecast = perpetual_experience_context_in_session(
            session,
            owner_id=admission["owner_id"],
            instrument_id=partition["instrument_id"],
            horizon=admission["context"]["horizon"],
            cutoff_at=cutoff,
            limit=3 - len(paper),
        )
        return (*paper, *forecast)

    return prepare_originals(database, read)


def freeze_experience_selection_in_session(session, request, candidates, *, recorded_at):
    """Task delivery receipt; deliberately does not assert model/skill consumption."""
    from ..ports.perpetual_paper_experience import SOURCE as PAPER_SOURCE

    mixed = any(item.source_id == PAPER_SOURCE for item in candidates)
    version = "v2" if mixed else "v1"
    effective = {item.evidence_id for item in request.input_evidence}
    body = dict(
        schema_version="perpetual-experience-task-selection." + version,
        run_id=request.run_id,
        request_id=request.request_id,
        task_hash=canonical_hash(request.model_dump(mode="json")),
        candidates=[{"ref": item.evidence_id, "hash": item.content_hash} for item in candidates],
        selected=[
            {"ref": item.evidence_id, "hash": item.content_hash}
            for item in candidates
            if item.evidence_id in effective
        ],
        excluded=[
            {
                "ref": item.evidence_id,
                "hash": item.content_hash,
                "reason": "context_selector_excluded",
            }
            for item in candidates
            if item.evidence_id not in effective
        ],
        provider_input_proven=False,
        skill_consumed=False,
        recorded_at=_time(recorded_at),
    )
    if mixed:
        kinds = {
            item.evidence_id: "paper_episode_net_cashflow"
            if item.source_id == PAPER_SOURCE
            else "forecast_terminal_direction"
            for item in candidates
        }
        admission = read_perpetual_admission_identity_in_session(session, request.run_id)[0]
        body.update(
            strategy="perpetual-history-inputs.v2",
            scope=admission["context"],
            cutoff_at=admission["admitted_at"],
        )
        for collection in ("candidates", "selected", "excluded"):
            for item in body[collection]:
                item["kind"] = kinds[item["ref"]]
    session.add(
        RunRoleOutputRecord(
            output_id=("perp_experience_selection_v2_" if mixed else "perp_experience_selection_")
            + canonical_hash(request.run_id),
            run_id=request.run_id,
            role="perpetual_experience_task_selection_" + version,
            schema_version=body["schema_version"],
            payload_json=canonical(body),
            payload_hash=canonical_hash(body),
            created_at=recorded_at,
        )
    )
