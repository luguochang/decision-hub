"""Explicit v2 forecast observations on the original immutable publication chain."""

import hashlib
import inspect
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.action_quote_repository import _capture  # pyright: ignore[reportPrivateUsage]
from ..persistence.action_repository import ActionRepository
from ..persistence.db import RawCaptureRecord, RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_close import request_cursor
from ..ports.perpetual_forecast_experience import (
    ROLE as LEGACY_ROLE,
)
from ..ports.perpetual_forecast_experience import (
    ForecastExperienceOriginal,
    classify_forecast_return,
    experience_original,
)
from ..ports.perpetual_forecast_scoring import METHOD, ROLE, SCHEMA, horizon_policy, score_forecast
from .market_original_parse import MarketOriginalParse
from .okx_perpetual_source import OKXPerpetualOriginalReader
from .perpetual_advice_query import (  # Original publication authentication boundary.
    PUBLISHER,
    PerpetualAdviceQuery,
    _original,  # pyright: ignore[reportPrivateUsage]
)
from .perpetual_forecast_truth import derive_truth, parse_truth_page
from .perpetual_research_binding import canonical, read_perpetual_admission_identity_in_session


def _time(value: datetime) -> str:
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _software() -> dict[str, str]:
    # Hash the actual files executing; old v1's four source inputs remain untouched.
    objects = {
        "application/perpetual_forecast_scoring.py": _software,
        "application/perpetual_forecast_truth.py": derive_truth,
        "ports/perpetual_forecast_scoring.py": score_forecast,
        "ports/perpetual_forecast_experience.py": classify_forecast_return,
        "ports/perpetual_close.py": request_cursor,
        "application/market_original_parse.py": MarketOriginalParse,
    }
    return {
        name: hashlib.sha256(Path(inspect.getfile(obj)).read_bytes()).hexdigest()
        for name, obj in objects.items()
    }


def _publication(
    session: Any, run_id: str, cutoff: datetime
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    identity = read_perpetual_admission_identity_in_session(session, run_id)
    if identity is None:
        raise ValueError("perpetual_scoring_admission_missing")
    admission, admission_ref = identity
    horizon = admission["context"]["horizon"]
    policy = horizon_policy(horizon)
    run = session.get(RunRecord, run_id, populate_existing=True)
    repo = ActionRepository(session)
    command = repo.get_command(
        PUBLISHER, "publish_" + canonical_hash([run_id, admission["opportunity_id"]])
    )
    if command is None:
        raise ValueError("perpetual_scoring_published_forecast_required")
    result = PerpetualAdviceQuery._published(  # pyright: ignore[reportPrivateUsage]
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
    label, family = materials["policies"]["label"], materials["policies"]["target_family"]
    expected_label = {
        "schema_version": "perpetual-label-rule.v1",
        "basis": "terminal_close_return",
        "up": "return_gt_threshold",
        "down": "return_lt_negative_threshold",
        "neutral": "inclusive_between_thresholds",
    }
    expected_family = {
        "schema_version": "perpetual-target-family.v1",
        "kind": "terminal_market_direction",
        "instrument_id": admission["partition"]["instrument_id"],
        "horizon": horizon,
        "duration_seconds": policy["minutes"] * 60,
    }
    if (
        label["body"] != expected_label
        or canonical_hash(label["body"]) != label["ref"]["hash"]
        or target["label_rule_ref"] != label["ref"]
        or target["target_family_ref"] != family["ref"]
        or canonical_hash(family["body"]) != family["ref"]["hash"]
        or family["body"] != expected_family
        or target["horizon"] != horizon
        or target["duration_seconds"] != policy["minutes"] * 60
        or target["price_basis"] != "trade_1m_close"
        or target["conditioning_ref"] is not None
    ):
        raise ValueError("perpetual_scoring_target_policy_unsupported")
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
        raise ValueError("perpetual_scoring_direction_target_or_condition_unverified")
    return admission, result, command


def _derive(
    session: Any,
    *,
    run_id: str,
    capture_ids: tuple[str, ...],
    archive: RawBodyArchive,
    instrument_reader: Any,
    recorded_at: datetime,
) -> ForecastExperienceOriginal:
    at = aware_utc(recorded_at)
    admission, publication, command = _publication(session, run_id, at)
    target = publication["target"]
    horizon = target["horizon"]
    if (
        type(capture_ids) is not tuple
        or not capture_ids
        or len(capture_ids) > horizon_policy(horizon)["max_pages"]
        or len(set(capture_ids)) != len(capture_ids)
    ):
        raise ValueError("perpetual_scoring_capture_list_invalid")
    start, end = (
        datetime.fromisoformat(target[key].replace("Z", "+00:00"))
        for key in ("window_start", "window_end")
    )
    if end > at:
        raise ValueError("perpetual_scoring_target_not_mature")
    contract = instrument_reader(
        session, DecisionSourceRefV1.model_validate(target["instrument_ref"])
    )
    if (
        "okx:" + contract.inst_id != admission["partition"]["instrument_id"]
        or contract.instrument_kind != "perpetual"
    ):
        raise ValueError("perpetual_scoring_instrument_mismatch")
    parser, pages = MarketOriginalParse(), []
    for capture_id in capture_ids:
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perpetual_scoring_capture_missing")
        pages.append(
            parse_truth_page(
                _capture(row),
                archive.read(row.body_ref),
                inst_id=contract.inst_id,
                as_of=at,
                selected_rows=parser.selected_rows,
            )
        )
    closes = derive_truth(
        pages, instrument_id=contract.inst_id, start=start, end=end, horizon=horizon, recorded_at=at
    )
    known = max(command.accepted_at, *(close.recorded_at for close in closes))
    if not end <= known <= at:
        raise ValueError("perpetual_scoring_label_clock_invalid")
    result, category = classify_forecast_return(
        closes[0].price, closes[-1].price, target["threshold_fraction"]
    )
    estimate, bindings = (
        publication["market_distribution"]["direction_distribution"],
        publication["snapshot"]["bindings"],
    )
    scores = score_forecast(estimate["probabilities"], category)
    body = dict(
        schema_version=SCHEMA,
        run_id=run_id,
        owner_id=admission["owner_id"],
        instrument_id=admission["partition"]["instrument_id"],
        horizon=horizon,
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
        **scores,
        strata={
            "asset": contract.base,
            "horizon": horizon,
            "event_family": None,
            "market_regime": None,
        },
        gaps=["event_family_unavailable", "market_regime_unavailable"]
        + (["direction_probabilities_unavailable"] if estimate["probabilities"] is None else []),
        capture_ids=list(capture_ids),
        archive_root=str(archive.root.resolve()),
        closes=[
            {
                key: _time(value) if isinstance(value, datetime) else value
                for key, value in asdict(close).items()
            }
            for close in closes
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


def read_scoring_experience_in_session(
    session: Any, reference: Any, *, cutoff_at: datetime
) -> ForecastExperienceOriginal:
    ref = DecisionSourceRefV1.model_validate(reference)
    row = session.get(RunRoleOutputRecord, ref.ref, populate_existing=True)
    if row is None:
        raise ValueError("perpetual_scoring_original_missing")
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
        raise ValueError("perpetual_scoring_original_invalid_or_future")
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
        raise ValueError("perpetual_scoring_original_changed")
    return rebuilt


class PerpetualForecastScoringService:
    def __init__(self, database: Any, instrument_source: Any, *, clock: Any = utcnow):
        if instrument_source.database is not database:
            raise ValueError("perpetual_scoring_database_mismatch")
        self.database, self.source, self.clock = database, instrument_source, clock

    read_in_session = staticmethod(read_scoring_experience_in_session)

    def record(self, *, run_id: str, capture_ids: tuple[str, ...]) -> ForecastExperienceOriginal:
        with self.database.session() as session:
            rows = session.scalars(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role.in_((ROLE, LEGACY_ROLE)),
                )
            ).all()
            if any(row.role == LEGACY_ROLE for row in rows):
                raise ValueError("perpetual_scoring_legacy_observation_preserved")
            if rows:
                row = rows[0]
                value = self.read_in_session(
                    session,
                    {"ref": row.output_id, "hash": row.payload_hash},
                    cutoff_at=self.clock(),
                )
                if value.body["capture_ids"] != list(capture_ids):
                    raise ValueError("perpetual_scoring_capture_conflict")
                return value
        prepared = prepare_originals(
            self.database,
            lambda session: _derive(
                session,
                run_id=run_id,
                capture_ids=capture_ids,
                archive=self.source.archive,
                instrument_reader=self.source.read_contract_in_session,
                recorded_at=self.clock(),
            ),
        )
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            recorded = aware_utc(self.clock())
            if recorded < datetime.fromisoformat(
                prepared.value.body["recorded_at"].replace("Z", "+00:00")
            ):
                raise ValueError("perpetual_scoring_record_clock_regressed")
            value = experience_original({**prepared.value.body, "recorded_at": _time(recorded)})
            rows = session.scalars(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role.in_((ROLE, LEGACY_ROLE)),
                )
            ).all()
            if rows:
                if (
                    len(rows) != 1
                    or rows[0].role != ROLE
                    or rows[0].payload_json != value.body_json
                ):
                    raise ValueError("perpetual_scoring_concurrent_original_conflict")
            else:
                session.add(
                    RunRoleOutputRecord(
                        output_id=value.ref.ref,
                        run_id=run_id,
                        role=ROLE,
                        schema_version=SCHEMA,
                        payload_json=value.body_json,
                        payload_hash=value.ref.hash,
                        created_at=recorded,
                    )
                )
                session.flush()
            prepared.verify_after_writes(session)
        return value
