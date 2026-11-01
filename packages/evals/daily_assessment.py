"""UNWIRED DRAFT: borrowed-snapshot Gate daily original replay.

No Session, database writer, adapter, HTTP request, model, partition loader or
wall clock is created here. The completed source replay does not attest the
implementation used when a historical DailyStrategyReceipt was produced.
A method is verified only through the actual immutable execution-admission role.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    FactEnvelope,
    ResearchReview,
    ResearchSessionResult,
    ResearchStrategyReceipt,
)
from packages.evals.input_receipts import verify_input_receipts
from packages.evals.prospective_observations import (
    _frozen_input,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.commit import (
    PUBLICATION_SOURCE_ROLE,  # pyright: ignore[reportPrivateUsage]
    _read_publication_source_in_session,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.daily_execution_binding import (
    read_daily_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.fact_lineage import digest, run_scope
from packages.kernel.decision_hub_kernel.application.fact_store import (
    _fact_model,  # pyright: ignore[reportPrivateUsage]
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.market_scope import outcome_scope_enabled
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    parse_graph_final_projection_declaration,
    parse_graph_final_ref,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.decision.daily_strategy import compute_daily_strategy
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    DataJobRecord,
    EvidenceParsedLinkRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    PublicationDecisionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    RunInputRefRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.provider_adapters.historical.gate_daily import normalize_gate_daily_payload
from packages.provider_adapters.historical.research import (
    _summary,  # pyright: ignore[reportPrivateUsage]
    _unit,  # pyright: ignore[reportPrivateUsage]
    loaded_daily_implementation,
)
from packages.provider_adapters.historical.runner import (
    _coverage_status,  # pyright: ignore[reportPrivateUsage]
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

DATASET = "crypto.btc_usdt.ohlcv_1d"
SOURCE = "gateio-public"
GATE_PATH = "/api/v4/spot/candlesticks"


@dataclass(frozen=True)
class DailySourceRef:
    role: str
    output_id: str
    schema_version: str
    payload_hash: str


@dataclass(frozen=True)
class DailySourceAssessment:
    source_status: Literal["verified", "unverifiable", "invalid"]
    reason: str | None
    stable_recipe_hash: str | None
    method_status: Literal["verified", "unverifiable", "invalid"]
    refs: tuple[DailySourceRef, ...] = ()
    limitations: tuple[str, ...] = ()


class _DailyError(ValueError):
    def __init__(self, reason: str, *, unverifiable: bool = False) -> None:
        super().__init__(reason)
        self.unverifiable = unverifiable


def _require(value: bool, reason: str, *, unverifiable: bool = False) -> None:
    if not value:
        raise _DailyError(reason, unverifiable=unverifiable)


def _utc(value: datetime | None) -> datetime:
    result = as_utc(value)
    _require(result is not None, "daily_original_timestamp_missing", unverifiable=True)
    assert result is not None
    return result


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        _require(key not in value, "daily_original_duplicate_json_key")
        value[key] = item
    return value


def _nonfinite(_: str) -> None:
    raise _DailyError("daily_original_nonfinite_json")


def _json(payload: str | bytes) -> Any:
    return json.loads(payload, object_pairs_hook=_pairs, parse_constant=_nonfinite)


def _ids(payload: str) -> list[str]:
    value = _json(payload)
    _require(
        type(value) is list and all(type(item) is str and item for item in value),
        "daily_original_reference_set_invalid",
    )
    _require(len(set(value)) == len(value), "daily_original_reference_set_invalid")
    return value


def _url_query(url: str, *, complete: bool) -> dict[str, str]:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    _require(
        parts.scheme == "https"
        and parts.netloc == "api.gateio.ws"
        and parts.path == GATE_PATH
        and not parts.fragment,
        "daily_gate_source_url_invalid",
    )
    _require(len({key for key, _ in pairs}) == len(pairs), "daily_gate_query_duplicate")
    query = dict(pairs)
    keys = (
        {"currency_pair", "interval", "from", "to"}
        if complete
        else {
            "currency_pair",
            "interval",
        }
    )
    _require(
        set(query) == keys and query["currency_pair"] == "BTC_USDT" and query["interval"] == "1d",
        "daily_gate_query_scope_invalid",
    )
    return query


def _parent(
    session: Session,
    outlook: AnalyticalOutlookRecord,
    assessed_at: datetime,
) -> tuple[RunRecord, ResearchSessionResult, tuple[DailySourceRef, ...]]:
    run = session.get(RunRecord, outlook.run_id, populate_existing=True)
    _require(run is not None, "daily_parent_run_missing", unverifiable=True)
    assert run is not None
    required = {"research_graph_final", "independent_reviewer", PUBLICATION_SOURCE_ROLE}
    rows = list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run.run_id,
                RunRoleOutputRecord.role.in_(required),
            )
            .execution_options(populate_existing=True)
        )
    )
    by_role = {row.role: row for row in rows}
    _require(
        required <= by_role.keys(), "daily_parent_publication_receipts_missing", unverifiable=True
    )
    _require(len(rows) == len(required), "daily_parent_publication_receipts_ambiguous")
    for row in rows:
        _require(
            row.payload_hash == hashlib.sha256(row.payload_json.encode()).hexdigest()
            and _utc(row.created_at) <= assessed_at,
            "daily_parent_receipt_invalid",
        )
    source = _json(by_role[PUBLICATION_SOURCE_ROLE].payload_json)
    reference = parse_graph_final_ref(source["graph_final_ref"])
    graph = by_role["research_graph_final"]
    projection = parse_graph_final_projection_declaration(
        graph.payload_json,
        schema_version=graph.schema_version,
    )
    _require(
        projection.run_id == run.run_id
        and projection.schema_version == graph.schema_version
        and reference.row.output_id == graph.output_id
        and reference.row.role == graph.role
        and reference.row.schema_version == graph.schema_version
        and reference.row.payload_hash == graph.payload_hash
        and _utc(reference.row.recorded_at) == _utc(graph.created_at),
        "daily_parent_graph_reference_invalid",
    )
    review = ResearchReview.model_validate_json(by_role["independent_reviewer"].payload_json)
    declared = projection.projected_result.model_copy(update={"reviews": [review]})
    _read_publication_source_in_session(
        session,
        run=run,
        reference=reference,
        declared=declared,
        expected_lease_owner=None,
        candidate=None,
        gate=None,
    )
    saved = session.get(ResearchResultRecord, run.run_id, populate_existing=True)
    publication = session.get(
        PublicationDecisionRecord, outlook.artifact_id, populate_existing=True
    )
    _require(
        saved is not None and publication is not None,
        "daily_parent_saved_result_missing",
        unverifiable=True,
    )
    assert saved is not None and publication is not None
    persisted = ResearchSessionResult.model_validate_json(saved.payload_json)
    frozen_at = _utc(publication.decided_at)
    _require(
        outlook.artifact_id == run.artifact_id
        and outlook.study_id == run.study_id
        and publication.run_id == run.run_id
        and outlook.result_hash
        == publication.result_hash
        == research_result_content_hash(persisted)
        and frozen_at == _utc(run.finished_at) == _utc(outlook.recorded_at)
        and frozen_at == _utc(outlook.window_start_at)
        and _utc(outlook.window_end_at) == frozen_at + timedelta(days=7)
        and frozen_at <= assessed_at
        and outlook.research_quality == persisted.final_coverage.status,
        "daily_outlook_parent_or_window_invalid",
    )
    refs = tuple(
        DailySourceRef(row.role, row.output_id, row.schema_version, row.payload_hash)
        for row in sorted(rows, key=lambda row: row.role)
    )
    return (
        run,
        persisted,
        (
            *refs,
            DailySourceRef(
                "research_result",
                run.run_id,
                saved.schema_version,
                saved.payload_hash,
            ),
        ),
    )


def _capture_rows(
    session: Session,
    capture: RawCaptureRecord,
    *,
    archive: RawBodyArchive,
    cutoff: datetime,
    selection_upper_bound: datetime,
) -> list[dict[str, object]]:
    parts = capture.context_id.split(":")
    _require(
        len(parts) == 4
        and parts[0] == "historical"
        and bool(parts[1])
        and parts[2].isdigit()
        and int(parts[2]) > 0
        and parts[3] == DATASET,
        "daily_capture_context_invalid",
    )
    job = session.get(DataJobRecord, parts[1], populate_existing=True)
    _require(job is not None, "daily_original_data_job_missing", unverifiable=True)
    assert job is not None
    datasets = _ids(job.dataset_ids_json)
    start, end = _utc(job.window_start_at), _utc(job.window_end_at)
    # This is the exact existing DataJobService._request_hash canonical payload.
    # Neither mutable job status nor the current attempt supplies old authority.
    _require(
        DATASET in datasets
        and job.request_hash
        == digest(
            {
                "dataset_ids": datasets,
                "requested_by": job.requested_by,
                "window_start_at": start.isoformat(),
                "window_end_at": end.isoformat(),
            }
        ),
        "daily_original_data_job_request_invalid",
    )
    query = _url_query(capture.url, complete=True)
    _require(
        query["from"] == str(int(start.timestamp()))
        and query["to"] == str(int(end.timestamp()) - 1),
        "daily_capture_window_invalid",
    )
    started, completed, recorded = map(
        _utc, (capture.started_at, capture.completed_at, capture.recorded_at)
    )
    _require(
        _utc(job.created_at) <= started <= completed <= recorded <= cutoff
        and recorded <= selection_upper_bound <= cutoff
        and capture.status_code == 200
        and capture.error_code is None
        and capture.body_ref is not None
        and capture.body_hash is not None,
        "daily_original_capture_invalid_or_late",
    )
    assert capture.body_ref is not None
    body = archive.read(capture.body_ref)
    _require(
        len(body) == capture.byte_count and hashlib.sha256(body).hexdigest() == capture.body_hash,
        "daily_original_capture_body_invalid",
    )
    payload = _json(body)
    # First supported protocol is the actual eight-column Gate spot daily body.
    # Other old shapes are retained but cannot be uniquely interpreted here.
    _require(
        type(payload) is list
        and all(
            type(row) is list and len(row) == 8 and all(type(item) is str for item in row)
            for row in payload
        ),
        "daily_gate_original_format_unverifiable",
        unverifiable=True,
    )
    earliest = normalize_gate_daily_payload(payload, start=start, end=end, closed_at=started)
    latest = normalize_gate_daily_payload(
        payload, start=start, end=end, closed_at=selection_upper_bound
    )
    # Old loader read now() per row after the HTTP response, before Fact producer.
    # This conservative bracket proves a unique row set, including midnight;
    # it does not guess its undocumented old instant or use today's wall clock.
    _require(earliest == latest, "daily_original_selection_clock_unverifiable", unverifiable=True)
    return earliest


def _fact_hash(model: FactEnvelope) -> str:
    keys = (
        "requirement_id",
        "metric_family",
        "field",
        "instrument",
        "venue",
        "value",
        "unit",
        "window_start_at",
        "window_end_at",
        "event_offset",
        "published_at",
        "source_id",
        "independence_group",
        "delay_class",
        "payload_schema_ref",
        "attributes",
    )
    return research_fact_payload_hash(**{key: getattr(model, key) for key in keys})


def _read_daily(
    session: Session,
    outlook: AnalyticalOutlookRecord,
    *,
    assessed_at: datetime,
    archive: RawBodyArchive,
) -> tuple[tuple[DailySourceRef, ...], str, str | None]:
    _require(
        (outlook.method_family, outlook.method_version, outlook.horizon)
        == (
            "daily_rule",
            "daily-trend-v0.1",
            "7d",
        ),
        "daily_method_scope_unverifiable",
        unverifiable=True,
    )
    _require(
        outlook.cohort == "prospective",
        "daily_retrospective_projection_unverifiable",
        unverifiable=True,
    )
    run, _, parent_refs = _parent(session, outlook, assessed_at)
    source_market, source_instrument = run_scope(session, run.run_id)
    _require(
        outlook.market == source_market == "crypto"
        and outlook.instrument == "BTC-USDT"
        and source_instrument in {"BTC-USDT", "BTC-USDT-SWAP"},
        "daily_target_scope_invalid",
    )
    frozen_at = _utc(outlook.window_start_at)
    evidence_ids = _ids(outlook.evidence_refs_json)
    _require(len(evidence_ids) == 1, "daily_evidence_set_unverifiable", unverifiable=True)
    evidence = session.get(ResearchEvidenceRecord, evidence_ids[0], populate_existing=True)
    _require(evidence is not None, "daily_original_evidence_missing", unverifiable=True)
    assert evidence is not None
    _require(
        evidence.run_id == run.run_id
        and evidence.quality == "accepted"
        and evidence.capability_id == "data.historical"
        and evidence.kind == "market"
        and evidence.authority == "exchange"
        and evidence.source_id == SOURCE
        and evidence.source_url is not None
        and evidence.published_at is None
        and _utc(evidence.observed_at) <= _utc(evidence.received_at) <= frozen_at
        and _utc(evidence.accepted_at) <= frozen_at,
        "daily_evidence_scope_or_time_invalid",
    )
    assert evidence.source_url is not None
    _url_query(evidence.source_url, complete=False)
    _require(
        evidence.content_hash
        == research_evidence_content_hash(
            requirement_id=evidence.requirement_id,
            kind=evidence.kind,
            authority=evidence.authority,
            source_id=evidence.source_id,
            source_url=evidence.source_url,
            published_at=None,
            excerpt=evidence.excerpt,
            structured_payload_ref=evidence.structured_payload_ref,
        )
        and evidence.evidence_id
        == research_evidence_instance_id(
            content_hash=evidence.content_hash,
            research_session_id=evidence.research_session_id,
            content_ref=evidence.content_ref,
            acquisition_receipt_id=evidence.acquisition_receipt_id,
        ),
        "daily_original_evidence_identity_invalid",
    )
    facts = list(
        session.scalars(
            select(ResearchFactRecord)
            .where(
                ResearchFactRecord.run_id == run.run_id,
                ResearchFactRecord.evidence_id == evidence.evidence_id,
                ResearchFactRecord.quality == "accepted",
            )
            .order_by(ResearchFactRecord.fact_id)
            .execution_options(populate_existing=True)
        )
    )
    _require(bool(facts), "daily_original_accepted_facts_missing", unverifiable=True)
    versions: list[str] = []
    version_refs: list[DailySourceRef] = []
    models: list[FactEnvelope] = []
    receipt_body = _json(outlook.raw_payload_json)
    receipt = ResearchStrategyReceipt.model_validate_json(outlook.raw_payload_json)
    _require(
        receipt.parameters == {"short_window": 20, "long_window": 60}
        and receipt.strategy_version == outlook.method_version
        and receipt.evidence_refs == evidence_ids,
        "daily_strategy_identity_invalid",
    )
    parsed_ids: set[str | None] = set()
    execution_claims: list[str | None] = []
    for fact in facts:
        model = _fact_model(fact)
        link = session.get(RunInputRefRecord, fact.fact_id, populate_existing=True)
        _require(link is not None, "daily_original_input_ref_missing", unverifiable=True)
        assert link is not None
        version = session.get(FactVersionRecord, link.fact_version_id, populate_existing=True)
        parsed_link = session.get(
            EvidenceParsedLinkRecord, fact.evidence_id, populate_existing=True
        )
        _require(
            version is not None and parsed_link is not None,
            "daily_original_fact_lineage_missing",
            unverifiable=True,
        )
        assert version is not None and parsed_link is not None
        # A future extension must explicitly authenticate rebinding of reused
        # instance IDs/receipts; it must not pretend fresh equality proves it.
        _require(
            link.use_mode == "fresh", "daily_reused_fact_rebinding_unverifiable", unverifiable=True
        )
        raw_version = _json(version.payload_json)
        FactEnvelope.model_validate_json(version.payload_json)
        _require(
            raw_version == model.model_dump(mode="json")
            and model.payload_hash == _fact_hash(model)
            and model.fact_id
            == research_fact_instance_id(
                evidence_id=model.evidence_id,
                payload_hash=model.payload_hash,
            )
            and fact.capability_id == "data.historical"
            and fact.source_id == SOURCE
            and fact.instrument == "BTC-USDT"
            and fact.venue == "gateio"
            and fact.metric_family == "crypto.spot.history"
            and fact.independence_group == f"{SOURCE}:{DATASET}"
            and fact.requirement_id == evidence.requirement_id
            and fact.delay_class == "historical"
            and fact.payload_schema_ref == "historical-window-summary.v1"
            and version.market == source_market
            and version.scope_instrument == source_instrument
            and version.original_fact_id == fact.fact_id
            and version.field == fact.field
            and version.source_id == fact.source_id
            and parsed_link.parsed_version_id == version.parsed_version_id
            and link.run_id == run.run_id,
            "daily_original_fact_or_version_invalid",
        )
        attrs = model.attributes
        _require(
            attrs.get("dataset_id") == DATASET
            and attrs.get("calculation_version") == "historical-window-summary.v1"
            and attrs.get("derived_from_hourly") is False
            and type(attrs.get("daily_strategy_receipt_json")) is str,
            "daily_original_fact_attributes_invalid",
        )
        strategy_claim = attrs["daily_strategy_receipt_json"]
        assert isinstance(strategy_claim, str)
        _require(_json(strategy_claim) == receipt_body, "daily_strategy_receipt_set_mismatch")
        execution_claim = attrs.get("daily_execution_receipt_json")
        _require(
            execution_claim is None or type(execution_claim) is str,
            "daily_execution_claim_shape_invalid",
        )
        assert execution_claim is None or isinstance(execution_claim, str)
        execution_claims.append(execution_claim)
        _require(
            _utc(model.observed_at) <= _utc(model.received_at) <= frozen_at
            and _utc(fact.accepted_at) <= frozen_at
            and _utc(version.known_at) <= frozen_at
            and _utc(version.observed_at) <= frozen_at
            and _utc(version.recorded_at) <= frozen_at
            and _utc(version.known_at)
            == max(
                _utc(fact.received_at),
                _utc(fact.accepted_at),
                _utc(evidence.received_at),
                _utc(evidence.accepted_at),
            )
            and _utc(version.observed_at) == _utc(fact.observed_at)
            and _utc(link.adopted_at) == _utc(fact.accepted_at)
            and _utc(link.as_of) <= frozen_at,
            "daily_original_fact_after_freeze",
        )
        semantic = digest(
            [
                source_market,
                source_instrument,
                model.metric_family,
                model.field,
                model.instrument,
                model.venue,
                model.unit,
                model.source_id,
                raw_version["window_start_at"],
                raw_version["window_end_at"],
                model.event_offset,
            ]
        )
        _require(
            version.semantic_key == semantic
            and version.fact_version_id
            == "fv_"
            + digest(
                [
                    semantic,
                    model.payload_hash,
                    version.parsed_version_id,
                    raw_version["observed_at"],
                ]
            )[:48],
            "daily_original_fact_version_identity_invalid",
        )
        versions.append(version.fact_version_id)
        parsed_ids.add(version.parsed_version_id)
        models.append(model)
        version_refs.append(
            DailySourceRef(
                "fact_version",
                version.fact_version_id,
                raw_version["schema_version"],
                digest(raw_version),
            )
        )
    _require(
        sorted(set(versions)) == _ids(outlook.fact_version_refs_json),
        "daily_outlook_fact_version_set_invalid",
    )
    _require(len(parsed_ids) == 1, "daily_multiple_parsed_inputs_unverifiable", unverifiable=True)
    parsed = session.get(ParsedVersionRecord, next(iter(parsed_ids)), populate_existing=True)
    _require(parsed is not None, "daily_original_parsed_version_missing", unverifiable=True)
    assert parsed is not None
    _require(
        parsed.content_hash == evidence.content_hash
        and parsed.archive_status == "captured"
        and _utc(parsed.recorded_at) <= frozen_at,
        "daily_original_parsed_version_invalid",
    )
    capture_ids = _ids(parsed.capture_ids_json)
    _require(len(capture_ids) == 1, "daily_capture_set_unverifiable", unverifiable=True)
    capture = session.get(RawCaptureRecord, capture_ids[0], populate_existing=True)
    _require(capture is not None, "daily_original_raw_capture_missing", unverifiable=True)
    assert capture is not None
    full_rows = _capture_rows(
        session,
        capture,
        archive=archive,
        cutoff=frozen_at,
        selection_upper_bound=min(_utc(item.observed_at) for item in models),
    )
    assert capture.body_hash is not None
    original_evidence = session.get(
        ResearchEvidenceRecord, parsed.original_evidence_id, populate_existing=True
    )
    _require(original_evidence is not None, "daily_parsed_origin_missing", unverifiable=True)
    assert original_evidence is not None
    parser_suffix = ":" + (original_evidence.parser_ref or "adapter")
    _require(
        parsed.parser_version.endswith(parser_suffix),
        "daily_original_parser_identity_unverifiable",
        unverifiable=True,
    )
    original_parser_version = parsed.parser_version[: -len(parser_suffix)]
    _require(
        original_evidence.content_hash == evidence.content_hash
        and parsed.content_ref == original_evidence.content_ref == evidence.content_ref
        and parsed.acquisition_receipt_id == original_evidence.acquisition_receipt_id
        and parsed.acquisition_receipt_id == evidence.acquisition_receipt_id
        and parsed.parsed_version_id
        == "parsed_"
        + digest(
            [
                original_evidence.content_hash,
                original_parser_version,
                original_evidence.parser_ref,
                sorted({capture.body_hash}),
                parsed.content_ref,
                parsed.acquisition_receipt_id,
            ]
        )[:48],
        "daily_original_parsed_identity_invalid",
    )
    first = models[0]
    start, end = _utc(first.window_start_at), _utc(first.window_end_at)
    _require(
        all(
            (_utc(item.window_start_at), _utc(item.window_end_at)) == (start, end)
            for item in models
        ),
        "daily_fact_window_set_invalid",
    )
    job_context = capture.context_id.split(":")
    job = session.get(DataJobRecord, job_context[1], populate_existing=True)
    assert job is not None
    _require(
        _utc(job.window_start_at) <= start < end <= _utc(job.window_end_at)
        and end <= min(_utc(item.observed_at) for item in models),
        "daily_fact_window_invalid",
    )
    rows = [
        row for row in full_rows if start <= datetime.fromisoformat(str(row["timestamp"])) < end
    ]
    summary = _summary(DATASET, rows)
    strategy_rows = [
        {
            "date": str(row["timestamp"])[:10],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume_base"],
        }
        for row in rows
    ]
    actual = compute_daily_strategy(
        strategy_rows,
        as_of=str(rows[-1]["timestamp"])[:10],
        evidence_refs=evidence_ids,
        short_window=20,
        long_window=60,
    )
    _require(actual.as_dict() == receipt_body, "daily_full_strategy_replay_mismatch")
    excerpt = _json(evidence.excerpt)
    _require(
        type(excerpt) is dict
        and set(excerpt)
        == {
            "dataset_id",
            "coverage_status",
            "requested_window",
            "actual_coverage",
            "summary",
            "gaps",
            "derived_from_hourly",
            "calculation_version",
        },
        "daily_evidence_excerpt_shape_invalid",
    )
    actual_start = datetime.fromisoformat(str(full_rows[0]["timestamp"])) if full_rows else None
    actual_end = datetime.fromisoformat(str(full_rows[-1]["timestamp"])) if full_rows else None
    coverage = _coverage_status(
        DATASET,
        _utc(job.window_start_at),
        _utc(job.window_end_at),
        actual_start,
        actual_end,
        len(full_rows),
    )
    selected_value = excerpt["summary"]
    _require(type(selected_value) is dict, "daily_evidence_summary_replay_mismatch")
    selected = cast(dict[str, Any], selected_value)
    _require(
        type(selected) is dict
        and bool(selected)
        and all(
            field in summary and type(value) is str and summary[field] == value
            for field, value in selected.items()
        )
        and excerpt["dataset_id"] == DATASET
        and excerpt["requested_window"] == [start.isoformat(), end.isoformat()]
        and excerpt["actual_coverage"]
        == [
            actual_start.isoformat() if actual_start else None,
            actual_end.isoformat() if actual_end else None,
        ]
        and excerpt["coverage_status"] == coverage
        and excerpt["gaps"]
        == (
            []
            if coverage == "complete"
            else [
                "actual_coverage_does_not_span_requested_window",
            ]
        )
        and excerpt["derived_from_hourly"] is False
        and excerpt["calculation_version"] == "historical-window-summary.v1",
        "daily_evidence_summary_replay_mismatch",
    )
    _require(
        all(
            item.field in selected
            and item.value == selected[item.field]
            and item.unit == _unit(DATASET, item.field, "usdt_and_btc")
            and item.attributes.get("coverage_status") == coverage
            for item in models
        ),
        "daily_full_fact_replay_mismatch",
    )
    action = {"bullish": "long", "bearish": "short", "range": "neutral", "unresolved": "no_trade"}[
        actual.outlook
    ]
    expected_reasons = []
    if not outcome_scope_enabled(outlook.market, outlook.instrument):
        expected_reasons.append("observation_market_not_enabled")
    _require(
        outlook.action == action
        and outlook.subjective_probability is None
        and outlook.probability_status == actual.probability_status == "unavailable"
        and outlook.trigger == actual.trigger
        and outlook.invalidation == actual.invalidation
        and _utc(outlook.data_as_of) == _utc(evidence.received_at)
        and _json(outlook.exclusion_reasons_json) == sorted(expected_reasons)
        and outlook.eligibility == ("excluded" if expected_reasons else "eligible"),
        "daily_outlook_projection_replay_mismatch",
    )
    frozen = _frozen_input(session, outlook)
    verify_input_receipts(session, {**frozen, "cutoff_at": frozen["recorded_at"]}, archive)
    _require(len(set(execution_claims)) == 1, "daily_execution_claim_set_mismatch")
    return (*parent_refs, *version_refs), evidence.evidence_id, execution_claims[0]


def assess_daily_source_in_session(
    session: Session,
    outlook: AnalyticalOutlookRecord,
    *,
    assessed_at: datetime,
    archive: RawBodyArchive | None,
) -> DailySourceAssessment:
    """Read originals only. Root retains independent observation/label handling.

    Old rows remain method unknown; current JSON claims never authorize themselves.
    """
    if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
        raise ValueError("assessment_time_timezone_required")
    if archive is None:
        return DailySourceAssessment(
            "unverifiable", "daily_raw_archive_unavailable", None, "unverifiable"
        )
    with session.no_autoflush:
        try:
            refs, evidence_id, execution_claim = _read_daily(
                session,
                outlook,
                assessed_at=assessed_at.astimezone(UTC),
                archive=archive,
            )
        except _DailyError as exc:
            return DailySourceAssessment(
                "unverifiable" if exc.unverifiable else "invalid", str(exc), None, "unverifiable"
            )
        except FileNotFoundError:
            return DailySourceAssessment(
                "unverifiable", "daily_raw_archive_missing", None, "unverifiable"
            )
        except (
            ValueError,
            PermissionError,
            KeyError,
            TypeError,
            OSError,
            IndexError,
            AttributeError,
        ) as exc:
            # The production integration must enumerate any existing original
            # missing-code cases as unverifiable; no generic exception is a pass.
            return DailySourceAssessment(
                "invalid", str(exc) or type(exc).__name__, None, "unverifiable"
            )
    if execution_claim is None:
        return DailySourceAssessment(
            "verified",
            None,
            None,
            "unverifiable",
            refs,
            ("original_execution_identity_unattested",),
        )
    # Source/parent have actually authenticated above, in this same snapshot.
    try:
        with session.no_autoflush:
            frozen = read_daily_execution_in_session(
                session,
                run_id=outlook.run_id,
                evidence_id=evidence_id,
                receipt_json=execution_claim,
                archive=archive,
                implementation=loaded_daily_implementation(),
                publication_at=_utc(outlook.recorded_at),
                assessed_at=assessed_at,
            )
    except (
        ValueError,
        PermissionError,
        KeyError,
        TypeError,
        OSError,
        IndexError,
        AttributeError,
    ) as exc:
        return DailySourceAssessment("verified", None, None, "invalid", refs, (str(exc),))
    if frozen is None:
        return DailySourceAssessment(
            "verified", None, None, "unverifiable", refs, ("daily_execution_admission_missing",)
        )
    return DailySourceAssessment(
        "verified",
        None,
        frozen.stable_recipe_hash,
        "verified",
        (
            *refs,
            DailySourceRef(
                frozen.role, frozen.output_id, frozen.schema_version, frozen.payload_hash
            ),
        ),
        ("deterministic_daily_rule_not_calibration",),
    )
