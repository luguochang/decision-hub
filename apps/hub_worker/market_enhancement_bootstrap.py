"""Normal worker entry for six independent descriptive enhancement candidates.

Only archived, accepted FRED versions with their original database first-known
binding are converted. No fetching, artificial vintages, fitted probabilities,
promotion, or background experiment is started here. Constructors and GETs do
not register candidates; start/process are explicit worker writes.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import AwareDatetime, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from packages.kernel.decision_hub_kernel.application.fact_lineage import digest, run_scope
from packages.kernel.decision_hub_kernel.application.fact_store import (
    _fact_model,  # pyright: ignore[reportPrivateUsage] # Authenticate the native typed original.
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.market_enhancements import (
    ArchivedEnhancementInputs,
    CrowdingInput,
    EnhancementBinding,
    EnhancementOriginalReader,
    EnhancementReceipt,
    EventSurpriseInput,
    MacroRelease,
    MacroStateInput,
    MarketEnhancements,
    macro_release_from_fact,
)
from packages.kernel.decision_hub_kernel.application.market_extension_registry import (
    MarketExtensionRegistry,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvidenceParsedLinkRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.market_extension import ExtensionAdmission
from packages.kernel.decision_hub_kernel.ports.market_path import (
    FrozenContract,
    aware_utc,
    canonical_hash,
    spot_scope,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

ROUTES = ("MACRO_STATE_V1", "CROWDING_V1", "EVENT_SURPRISE_V1")
SCHEMA = "market-enhancement-job-receipt.v1"
SERIES = {"DGS2", "DGS10", "DTWEXBGS"}
HASH = r"^[a-f0-9]{64}$"


class FredOriginalBinding(FrozenContract):
    fact_version_id: str
    fact_id: str
    evidence_id: str
    payload_hash: str = Field(pattern=HASH)
    parsed_version_id: str
    known_at: AwareDatetime
    series_id: str
    observation_date: date
    source_market: str
    source_instrument: str
    captures: tuple[dict, ...] = Field(min_length=1, max_length=8)


class EnhancementJobReceipt(FrozenContract):
    schema_version: Literal["market-enhancement-job-receipt.v1"] = SCHEMA
    receipt_id: str
    scope_id: str
    registration_id: str
    manifest_hash: str = Field(pattern=HASH)
    input_ref: str
    input_hash: str = Field(pattern=HASH)
    cutoff_at: AwareDatetime
    saved_at: AwareDatetime
    result: EnhancementReceipt
    admission: ExtensionAdmission
    fred_bindings: tuple[FredOriginalBinding, ...] = Field(max_length=128)
    scanned_fred_versions: int = Field(ge=0, le=128)
    excluded_fred_versions: int = Field(ge=0, le=128)
    exclusion_reasons: tuple[str, ...] = Field(max_length=16)
    scan_truncated: bool
    probability_qualified: Literal[False] = False
    promotion: Literal[False] = False


def receipt_run_id(scope_id: str) -> str:
    return "enhancements:" + scope_id


def receipt_role(route: str, identity: str) -> str:
    return "enhance_" + route.lower() + "_" + canonical_hash([identity])[:24]


def read_enhancement_receipt(row: RunRoleOutputRecord) -> EnhancementJobReceipt:
    value = EnhancementJobReceipt.model_validate_json(row.payload_json)
    body = value.model_dump(mode="json")
    if (
        row.schema_version != SCHEMA
        or row.output_id != value.receipt_id
        or row.run_id != receipt_run_id(value.scope_id)
        or row.role != receipt_role(value.result.route, value.receipt_id)
        or row.payload_json != canonical_recipe(body)
        or row.payload_hash != canonical_hash(body)
        or as_utc(row.created_at) != value.saved_at
        or value.saved_at < value.cutoff_at
        or value.scope_id != spot_scope(value.result.instrument).scope_id
        or value.result.cutoff_at != value.cutoff_at
        or value.result.input_hash != value.input_hash
        or value.admission.registration_id != value.registration_id
        or value.admission.scope_id != value.scope_id
        or value.admission.manifest_hash != value.manifest_hash
        or value.admission.checked_at > value.saved_at
        or any(binding.known_at > value.cutoff_at for binding in value.fred_bindings)
    ):
        raise ValueError("enhancement_saved_original_invalid")
    return value


def _clock(value: datetime | None) -> datetime:
    normalized = as_utc(value)
    if normalized is None:
        raise ValueError("enhancement_fred_original_clock_missing")
    return normalized


def _fred_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "fred.stlouisfed.org"
        and parsed.path == "/graph/fredgraph.csv"
    )


def _authenticated_fred(session, version, archive: RawBodyArchive, cutoff: datetime, budget=None):
    """Authenticate exact Fact/Evidence/Parsed/capture bytes; preserve native known_at."""
    fact = session.get(ResearchFactRecord, version.original_fact_id)
    evidence = session.get(ResearchEvidenceRecord, fact.evidence_id) if fact else None
    parsed = session.get(ParsedVersionRecord, version.parsed_version_id)
    link = session.get(EvidenceParsedLinkRecord, fact.evidence_id) if fact else None
    if not fact or not evidence or not parsed or not link:
        raise ValueError("enhancement_fred_lineage_unavailable")
    model = _fact_model(fact)
    payload = model.model_dump(mode="json")
    if model.quality != "accepted" or evidence.quality != "accepted":
        raise ValueError("enhancement_fred_fact_or_evidence_not_accepted")
    known = max(
        _clock(fact.received_at),
        _clock(fact.accepted_at),
        _clock(evidence.received_at),
        _clock(evidence.accepted_at),
    )
    source_market, source_instrument = run_scope(session, fact.run_id)
    semantic = digest(
        [
            source_market,
            source_instrument,
            fact.metric_family,
            fact.field,
            fact.instrument,
            fact.venue,
            fact.unit,
            fact.source_id,
            payload["window_start_at"],
            payload["window_end_at"],
            fact.event_offset,
        ]
    )
    expected_id = (
        "fv_"
        + digest(
            [
                semantic,
                fact.payload_hash,
                parsed.parsed_version_id,
                payload["observed_at"],
            ]
        )[:48]
    )
    expected_fact_hash = research_fact_payload_hash(
        **{
            key: getattr(model, key)
            for key in (
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
        }
    )
    expected_evidence_hash = research_evidence_content_hash(
        requirement_id=evidence.requirement_id,
        kind=evidence.kind,
        authority=evidence.authority,
        source_id=evidence.source_id,
        source_url=evidence.source_url,
        published_at=as_utc(evidence.published_at),
        excerpt=evidence.excerpt,
        structured_payload_ref=evidence.structured_payload_ref,
    )
    if (
        payload != json.loads(version.payload_json)
        or model.quality != "accepted"
        or evidence.quality != "accepted"
        or fact.run_id != evidence.run_id
        or version.fact_version_id != expected_id
        or version.semantic_key != semantic
        or version.market != source_market
        or version.scope_instrument != source_instrument
        or source_market != "crypto"
        or version.source_id != "fred-public"
        or version.field != "level"
        or model.instrument not in SERIES
        or expected_fact_hash != fact.payload_hash
        or fact.fact_id
        != research_fact_instance_id(
            evidence_id=fact.evidence_id,
            payload_hash=fact.payload_hash,
        )
        or expected_evidence_hash != evidence.content_hash
        or parsed.content_hash != evidence.content_hash
        or link.parsed_version_id != parsed.parsed_version_id
        or parsed.archive_status != "captured"
        or known != _clock(version.known_at)
        or _clock(version.observed_at) != model.observed_at
        or model.observed_at > model.received_at
        or model.received_at > known
        or _clock(evidence.observed_at) > _clock(evidence.received_at)
        or any(
            _clock(t) > cutoff
            for t in (
                version.known_at,
                version.recorded_at,
                parsed.recorded_at,
                fact.accepted_at,
                evidence.accepted_at,
                fact.received_at,
                evidence.received_at,
            )
        )
        or not evidence.source_url
        or not _fred_url(evidence.source_url)
        or parse_qs(urlsplit(evidence.source_url).query).get("id") != [model.instrument]
    ):
        raise ValueError("enhancement_fred_original_binding_invalid")
    attrs = model.attributes
    declared = json.loads(evidence.excerpt)
    if not isinstance(declared, dict):
        raise ValueError("enhancement_fred_evidence_shape_invalid")
    legacy = model.payload_schema_ref == "fred.series-observation.v1"
    if model.payload_schema_ref not in {"fred.series-observation.v1", "fred.series-observation.v2"}:
        raise ValueError("enhancement_fred_fact_schema_unsupported")
    # Old v1 retained the date in authenticated evidence, not fact attributes.
    # Its published_at was the observation-day midnight and is not a public clock.
    observation_date = declared.get("observation_date") if legacy else attrs.get("observation_date")
    try:
        if (
            not isinstance(observation_date, str)
            or date.fromisoformat(observation_date).isoformat() != observation_date
        ):
            raise ValueError("invalid_date")
    except ValueError as exc:
        raise ValueError("enhancement_fred_observation_date_invalid") from exc
    if (
        declared.get("series") != model.instrument
        or attrs.get("series") != model.instrument
        or declared.get("observation_date") != observation_date
        or Decimal(str(declared.get("value"))) != Decimal(str(model.value))
    ):
        raise ValueError("enhancement_fred_evidence_value_mismatch")
    capture_ids = json.loads(parsed.capture_ids_json)
    if not isinstance(capture_ids, list) or not 1 <= len(capture_ids) <= 8:
        raise ValueError("enhancement_fred_capture_unavailable")
    captures = []
    for identity in capture_ids:
        capture = session.get(RawCaptureRecord, identity)
        if (
            not capture
            or capture.status_code != 200
            or capture.error_code
            or not capture.body_ref
            or not capture.body_hash
            or not _fred_url(capture.url)
            or not 0 < capture.byte_count <= 2 * 1024 * 1024
            or not _clock(capture.started_at) <= _clock(capture.completed_at) <= known
            or _clock(capture.recorded_at) > cutoff
        ):
            continue
        if budget is not None:
            if budget[0] + capture.byte_count > 16 * 1024 * 1024:
                raise ValueError("enhancement_fred_raw_scan_budget_exhausted")
            budget[0] += capture.byte_count
        raw = archive.read(capture.body_ref)
        if len(raw) != capture.byte_count or hashlib.sha256(raw).hexdigest() != capture.body_hash:
            raise ValueError("enhancement_fred_raw_hash_mismatch")
        rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        matched = [
            row[model.instrument]
            for row in rows
            if (row.get("DATE") or row.get("observation_date")) == observation_date
            and row.get(model.instrument) not in {None, "", "."}
        ]
        if not matched:
            continue
        if any(Decimal(value) != Decimal(str(model.value)) for value in matched):
            raise ValueError("enhancement_fred_raw_value_mismatch")
        captures.append(
            {
                "capture_id": capture.capture_id,
                "body_ref": capture.body_ref,
                "body_hash": capture.body_hash,
                "completed_at": _clock(capture.completed_at).isoformat(),
                "recorded_at": _clock(capture.recorded_at).isoformat(),
            }
        )
    if not captures:
        raise ValueError("enhancement_fred_matching_raw_unavailable")
    payload_hash = canonical_hash(payload)
    if legacy:
        release = MacroRelease.model_validate(
            {
                "series_id": model.instrument,
                "observation_date": observation_date,
                "value": model.value,
                "unit": model.unit,
                "vintage_at": None,
                "source": {
                    "original_ref": "fact-version:" + version.fact_version_id,
                    "original_hash": payload_hash,
                    "source_id": model.source_id,
                    "observed_at": model.observed_at,
                    "received_at": model.received_at,
                    "known_at": known,
                    "public_at": None,
                    "public_precision": "unknown",
                },
            }
        )
    else:
        release = macro_release_from_fact(
            payload,
            original_ref="fact-version:" + version.fact_version_id,
            original_hash=payload_hash,
            known_at=known,
        )
    return release, FredOriginalBinding(
        fact_version_id=version.fact_version_id,
        fact_id=fact.fact_id,
        evidence_id=evidence.evidence_id,
        payload_hash=payload_hash,
        parsed_version_id=parsed.parsed_version_id,
        known_at=known,
        series_id=release.series_id,
        observation_date=release.observation_date,
        source_market=source_market,
        source_instrument=source_instrument,
        captures=tuple(captures),
    )


class MarketEnhancementTick:
    def __init__(self, database: Database, data_root: Path, *, enabled=True, clock=utcnow):
        self.database, self.enabled, self.clock = database, enabled, clock
        self.archive = RawBodyArchive(data_root / "raw-captures")
        self.service = MarketEnhancements(ArchivedEnhancementInputs(self.archive))
        self.registry = MarketExtensionRegistry(database, clock=clock)
        self._started = False
        self._start_lock = threading.Lock()

    def start(self) -> int:
        """Worker startup only. Restart uses exact manifest/request IDs, no pointer activation."""
        if not self.enabled:
            return 0
        with self._start_lock:
            if self._started:
                return 0
            for instrument in ("BTC-USDT", "ETH-USDT"):
                self.service.register_candidates(self.registry, instrument)
            self._started = True
        return 6

    @contextmanager
    def _fred_read_snapshot(self):
        """One physical file-RO snapshot; CSV verification never reserves a writer."""
        if self.database.engine.dialect.name != "sqlite":
            raise ValueError("enhancement_physical_sqlite_required")
        name = self.database.engine.url.database
        if not name or name == ":memory:":
            raise ValueError("enhancement_physical_sqlite_required")
        path = Path(name).resolve(strict=True)
        before = path.stat()
        if not path.is_file():
            raise ValueError("enhancement_physical_sqlite_required")
        identity = before.st_dev, before.st_ino
        engine = create_engine(
            "sqlite+pysqlite://", poolclass=NullPool,
            creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5),
        )
        try:
            with Session(engine, autoflush=False) as session:
                connection = session.connection()
                connection.exec_driver_sql("PRAGMA query_only=ON")
                connection.exec_driver_sql("BEGIN")
                native = connection.connection.driver_connection
                if not isinstance(native, sqlite3.Connection):
                    raise ValueError("enhancement_native_read_snapshot_required")
                changes = native.total_changes

                def check():
                    actual = path.stat()
                    databases = native.execute("PRAGMA database_list").fetchall()
                    main = [row for row in databases if row[1] == "main"]
                    if (
                        not native.in_transaction
                        or native.total_changes != changes
                        or native.execute("PRAGMA query_only").fetchone() != (1,)
                        or len(main) != 1
                        or Path(main[0][2]).resolve(strict=True) != path
                        or (actual.st_dev, actual.st_ino) != identity
                    ):
                        raise ValueError("enhancement_read_snapshot_changed")

                try:
                    check()
                    yield session
                    check()
                finally:
                    session.rollback()
        finally:
            engine.dispose()

    def process(self) -> int:
        if not self.enabled:
            return 0
        self.start()
        cutoff = aware_utc(self.clock())
        releases, bindings, reasons = [], [], []
        raw_budget = [0]
        with self._fred_read_snapshot() as session:
            rows = session.scalars(
                select(FactVersionRecord)
                .where(
                    FactVersionRecord.source_id == "fred-public",
                    FactVersionRecord.known_at <= cutoff,
                    FactVersionRecord.recorded_at <= cutoff,
                )
                .order_by(FactVersionRecord.known_at.desc(), FactVersionRecord.fact_version_id)
                .limit(129)
            ).all()
            for version in rows[:128]:
                try:
                    release, binding = _authenticated_fred(
                        session,
                        version,
                        self.archive,
                        cutoff,
                        raw_budget,
                    )
                except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
                    reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                    reasons.append(reason[:128])
                else:
                    releases.append(release)
                    bindings.append(binding)
        completed = 0
        for instrument in ("BTC-USDT", "ETH-USDT"):
            data = (
                MacroStateInput(instrument=instrument, releases=tuple(releases)),
                CrowdingInput(instrument=instrument, venues=()),
                EventSurpriseInput(instrument=instrument),
            )
            for manifest, typed_input in zip(self.service.manifests(instrument), data, strict=True):
                raw = canonical_recipe(typed_input.model_dump(mode="json")).encode()
                input_hash = hashlib.sha256(raw).hexdigest()
                scope_id = spot_scope(instrument).scope_id
                identity = (
                    "enhancement_receipt_"
                    + canonical_hash(
                        [
                            manifest.payload_hash,
                            input_hash,
                            [b.model_dump(mode="json") for b in bindings]
                            if manifest.route == "MACRO_STATE_V1"
                            else [],
                            list(dict.fromkeys(reasons))
                            if manifest.route == "MACRO_STATE_V1"
                            else [],
                            len(rows) if manifest.route == "MACRO_STATE_V1" else 0,
                        ]
                    )[:40]
                )
                with self.database.session() as session:
                    prior = session.get(RunRoleOutputRecord, identity)
                    if prior is not None:
                        read_enhancement_receipt(prior)
                        continue
                digest_value, reference = self.archive.put(raw)
                if digest_value != input_hash:
                    raise ValueError("enhancement_input_archive_hash_mismatch")
                result = self.service.evaluate(
                    EnhancementBinding(
                        route=manifest.route,
                        instrument=instrument,
                        input_ref=reference,
                        input_hash=input_hash,
                        cutoff_at=cutoff,
                    )
                )
                registration_id = self.registry.register(
                    "enhancement:" + manifest.payload_hash,
                    manifest,
                )
                binding = EnhancementBinding(
                    route=manifest.route,
                    instrument=instrument,
                    input_ref=reference,
                    input_hash=input_hash,
                    cutoff_at=cutoff,
                )
                assessing_registry = MarketExtensionRegistry(
                    self.database,
                    clock=self.clock,
                    original_reader=EnhancementOriginalReader(self.service, (binding,)),
                )
                admission = assessing_registry.assess(registration_id)
                value = EnhancementJobReceipt(
                    receipt_id=identity,
                    scope_id=scope_id,
                    registration_id=registration_id,
                    manifest_hash=manifest.payload_hash,
                    input_ref=reference,
                    input_hash=input_hash,
                    cutoff_at=cutoff,
                    saved_at=aware_utc(self.clock()),
                    result=result,
                    admission=admission,
                    fred_bindings=tuple(bindings) if manifest.route == "MACRO_STATE_V1" else (),
                    scanned_fred_versions=min(len(rows), 128)
                    if manifest.route == "MACRO_STATE_V1"
                    else 0,
                    excluded_fred_versions=len(reasons)
                    if manifest.route == "MACRO_STATE_V1"
                    else 0,
                    exclusion_reasons=tuple(dict.fromkeys(reasons))[:16]
                    if manifest.route == "MACRO_STATE_V1"
                    else (),
                    scan_truncated=manifest.route == "MACRO_STATE_V1"
                    and (
                        len(rows) > 128 or "enhancement_fred_raw_scan_budget_exhausted" in reasons
                    ),
                )
                body = canonical_recipe(value.model_dump(mode="json"))
                with self.database.session() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    prior = session.get(RunRoleOutputRecord, identity)
                    if prior is not None:
                        read_enhancement_receipt(prior)
                        continue
                    session.add(
                        RunRoleOutputRecord(
                            output_id=identity,
                            run_id=receipt_run_id(scope_id),
                            role=receipt_role(manifest.route, identity),
                            schema_version=SCHEMA,
                            payload_json=body,
                            payload_hash=canonical_hash(value.model_dump(mode="json")),
                            created_at=value.saved_at,
                        )
                    )
                completed += 1
        return completed

    async def tick(self) -> int:
        task = asyncio.create_task(asyncio.to_thread(self.process))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise
