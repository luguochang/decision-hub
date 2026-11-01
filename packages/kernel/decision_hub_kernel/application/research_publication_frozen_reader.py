"""Invoke the product's registered original reader on full frozen SQL material.

There is no bundle-code loader, shape cache, current-DB supplement or latest
selection fallback. R1's finite support remains a deliberate admission limit.
This module is an uninstalled R2 candidate, not a financial qualification.
"""

from __future__ import annotations

import hashlib
import json
import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..persistence.db import RunRecord, RunRoleOutputRecord
from ..persistence.frozen_publication_material import authenticated_material_session, verify_material_file
from ..ports.research_publication_bundle import FrozenPublicationBundleV1
from . import research_quality_view as original_reader
from .commit import PUBLICATION_SOURCE_ROLE
from ..persistence.publication_dependency_native import Limits, NativeCollector, SourceFile, canonical, cell, sha


@dataclass(frozen=True)
class OriginalPublicationRead:
    quality_json: str
    event_id: str
    artifact_id: str
    reader_source_sha256: str


def read_registered_original(session: Session, run_id: str, *, as_of: datetime,
                             reader_module=None) -> OriginalPublicationRead:
    """All dependent SQL happens before the unchanged original's exit guards."""
    registered = original_reader if reader_module is None else reader_module
    owner = registered.consume_published_research_originals_in_session
    owner_code = owner.__code__
    source_path = Path(registered.__file__).resolve(strict=True)
    before = hashlib.sha256(source_path.read_bytes()).hexdigest()

    def consume(quality, _mapped):
        if quality.status != "available":
            raise ValueError("publication_bundle_original_not_available")
        run = session.get(RunRecord, run_id, populate_existing=True)
        sources = list(session.scalars(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE
        ).execution_options(populate_existing=True)))
        if (run is None or run.status not in {"completed", "degraded"}
                or not run.artifact_id or len(sources) != 1
                or quality.publication_ref is None
                or sources[0].output_id != quality.publication_ref.output_id
                or sources[0].payload_hash != quality.publication_ref.payload_hash):
            raise ValueError("publication_bundle_terminal_publication_changed")
        return OriginalPublicationRead(quality.model_dump_json(), run.event_id, run.artifact_id, before)

    result = owner(session, run_id, as_of=as_of, consume=consume)
    if (registered.consume_published_research_originals_in_session is not owner
            or owner.__code__ is not owner_code
            or hashlib.sha256(source_path.read_bytes()).hexdigest() != before):
        raise ValueError("publication_bundle_registered_reader_changed")
    return result


def read_registered_original_v2(session: Session, run_id: str, *, as_of: datetime,
                                source_lease, reader_module=None) -> OriginalPublicationRead:
    """Explicit V2 lease; all original native permission/semantic reads remain."""
    registered = original_reader if reader_module is None else reader_module
    source_lease.require_read(session, run_id)
    source_lease.require_reader_source(registered.__file__)
    result = read_registered_original(session, run_id, as_of=as_of, reader_module=registered)
    source_lease.require_read(session, run_id)
    return result


def compare_frozen_original(
    frozen_session: Session, *, run_id: str, as_of: datetime,
    expected: OriginalPublicationRead, captured: dict[str, Any], limits: Limits,
) -> OriginalPublicationRead:
    """Fail before unknown ORM SQL; compare native full ranges after the read.

    The caller supplies a freshly opened, isolated material database. Its path
    is never copied from a bundle URL and no real database fallback is opened.
    Native SELECT outside SQLAlchemy is also detected by the R1 census.
    """
    if (captured.get("schema_version") != "research-publication-dependency-candidate.r1"
            or captured.get("observed_queries_status") != "complete"
            or captured.get("unresolved") or captured.get("native_changes") != 0):
        raise ValueError("publication_bundle_material_closure_incomplete")
    queries = {row["query_id"]: row for row in captured["queries"]}
    if len(queries) != len(captured["queries"]):
        raise ValueError("publication_bundle_material_query_ambiguous")
    connection = frozen_session.connection()

    def known_sql(actual, _cursor, statement, parameters, _context, many):
        if actual is not connection or many or not isinstance(parameters, tuple | list):
            raise ValueError("publication_bundle_frozen_connection_changed")
        typed = [{"python_type": type(value).__name__, "cell": cell(value, binding=True).json()}
                 for value in parameters]
        if sha(canonical([statement, typed])) not in queries:
            raise ValueError("publication_bundle_unrecorded_query")

    event.listen(connection, "before_cursor_execute", known_sql)
    try:
        with NativeCollector(frozen_session, limits=limits, trace_slot_owned=True) as observed:
            result = read_registered_original(frozen_session, run_id, as_of=as_of)
        actual = observed.snapshot()
    finally:
        event.remove(connection, "before_cursor_execute", known_sql)
    if actual["observed_queries_status"] != "complete":
        raise ValueError("publication_bundle_frozen_unresolved_query")
    for row in actual["queries"]:
        expected_query = queries[row["query_id"]]
        if any(row[name] != expected_query[name] for name in (
            "statement", "typed_parameters", "result", "table", "range_statement",
            "range_typed_parameters", "range_result", "member_identities", "empty_range",
        )):
            raise ValueError("publication_bundle_frozen_native_range_changed")
    if result != expected:
        raise ValueError("publication_bundle_frozen_original_changed")
    return result


def frozen_bundle_from_verified_read(
    original: OriginalPublicationRead, replayed: OriginalPublicationRead, *,
    material_ref: str, material_sha256: str, material_bytes: int,
) -> FrozenPublicationBundleV1:
    """Only complete comparison callers should hand this value to the producer.

    Storage must additionally require its internal preparation owner/claim;
    this value itself never grants permission to save, confirm or serve.
    """
    if original != replayed:
        raise ValueError("publication_bundle_frozen_original_changed")
    quality = original_reader.ResearchQualityViewV1.model_validate_json(original.quality_json)
    assert quality.publication_ref is not None and quality.recipe_selection_ref is not None
    assert quality.published_at is not None and quality.decision is not None
    report = quality.decision.full_report_json.encode("utf-8")
    return FrozenPublicationBundleV1(
        run_id=quality.run_id, event_id=original.event_id, artifact_id=original.artifact_id,
        publication_ref=quality.publication_ref, recipe_selection_ref=quality.recipe_selection_ref,
        original_published_at=quality.published_at, original_read_at=quality.as_of,
        quality_json=original.quality_json, report_sha256=hashlib.sha256(report).hexdigest(),
        report_bytes=len(report), material_ref=material_ref, material_sha256=material_sha256,
        material_bytes=material_bytes,
        trusted_reader_source_sha256=original.reader_source_sha256,
    )


@dataclass(frozen=True)
class PreparedFrozenPublication:
    """An issuer-bound one-call object; persistence does not trust caller JSON."""
    nonce: str
    value: FrozenPublicationBundleV1


def require_original_material_matches(live: dict, captured: dict) -> None:
    if (live.get("observed_queries_status") != "complete"
            or captured.get("observed_queries_status") != "complete"
            or live.get("native_changes") != 0 or captured.get("native_changes") != 0
            or live.get("unresolved") or captured.get("unresolved")):
        raise ValueError("publication_bundle_original_dependency_unresolved")
    expected_queries = {q["query_id"]: q for q in captured["queries"]}
    if len(expected_queries) != len(captured["queries"]):
        raise ValueError("publication_bundle_material_query_ambiguous")
    for query in live["queries"]:
        expected = expected_queries.get(query["query_id"])
        if expected is None or any(query[field] != expected[field] for field in (
            "statement", "typed_parameters", "result", "range_statement", "range_typed_parameters",
            "range_result", "member_identities", "empty_range"
        )):
            raise ValueError("publication_bundle_material_original_changed")
    for scope in ("schemas", "rows"):
        key = ((lambda value: value["table"]) if scope == "schemas"
               else (lambda value: (value["table"], value["primary_identity"])))
        original, material = ({key(v): v for v in live[scope]}, {key(v): v for v in captured[scope]})
        if (len(original) != len(live[scope]) or len(material) != len(captured[scope])
                or original != material):
            raise ValueError("publication_bundle_material_native_schema_changed")


class FrozenPublicationPreparer:
    def __init__(self, *, trusted_root: Path):
        self.root = trusted_root.resolve(strict=True)
        self._issued: dict[str, tuple[PreparedFrozenPublication, Path, str, dict]] = {}

    def _require_full_source_closure(self, captured: dict) -> None:
        """Release blocker, not a caller boolean or supplied permission token.

        Native ranges and nonempty caller SourceFile bytes do not prove the
        whole resource closure. The next candidate must derive the exact set
        from normal current release+manifest and selected installation index,
        and authenticate its identity/set before this issuer can mint proof.
        Until that resolver exists this business candidate refuses issuance.
        Private lifecycle controls explicitly inject their lower-level seam.
        """
        raise ValueError("publication_bundle_full_source_closure_unverified")

    def _sources(self, captured: dict) -> None:
        if not captured.get("sources"):
            raise ValueError("publication_bundle_material_source_inventory_missing")
        seen = set()
        for source in captured["sources"]:
            relative = source["relative_ref"]
            if (not isinstance(relative, str) or relative in seen
                    or any(p in {"", ".", ".."} for p in relative.split("/"))
                    or ":" in relative or "\\" in relative
                    or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])):
                raise ValueError("publication_bundle_material_source_invalid")
            seen.add(relative)
            raw = base64.b64decode(source["base64"], validate=True)
            if sha(raw) != source["sha256"] or len(raw) != source["bytes"]:
                raise ValueError("publication_bundle_material_source_changed")
            path = self.root
            for part in relative.split("/"):
                path = path / part
                stat = path.lstat()
                if path.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                    raise ValueError("publication_bundle_material_source_link")
            if sha(path.read_bytes()) != source["sha256"]:
                raise ValueError("publication_bundle_material_source_changed")

    def prepare(self, original_session: Session, *,
                run_id: str, as_of: datetime, material_path: Path,
                material_stage: Path,
                limits: Limits, max_material_bytes: int) -> PreparedFrozenPublication:
        """The original is borrowed RO; the frozen database is owned and private.

        Recheck R1 against the original now; older separate-snapshot fixtures
        cannot silently become a qualified current material capture.
        """
        if (type(max_material_bytes) is not int or max_material_bytes < 1
                or material_path.stat().st_size > max_material_bytes):
            raise ValueError("publication_bundle_explicit_material_bound_exceeded")
        with material_path.open("rb") as stream:
            raw = stream.read(max_material_bytes + 1)
        if len(raw) > max_material_bytes:
            raise ValueError("publication_bundle_explicit_material_bound_exceeded")
        verify_material_file(material_path, expected_sha256=sha(raw),
            expected_bytes=len(raw), max_bytes=max_material_bytes)
        captured = json.loads(raw)
        if (captured.get("schema_version") != "research-publication-dependency-candidate.r1"
                or captured.get("observed_queries_status") != "complete"
                or captured.get("native_changes") != 0 or captured.get("unresolved")):
            raise ValueError("publication_bundle_material_closure_incomplete")
        self._sources(captured)
        with NativeCollector(original_session, limits=limits, trace_slot_owned=True) as collection:
            original = read_registered_original(original_session, run_id, as_of=as_of)
        live_capture = collection.snapshot()
        # Authenticate all material DDL/native rows before executing its DDL.
        # R1 captures the full predicate membership, including invisible
        # columns and empty ranges. No free-standing fixture is adopted.
        require_original_material_matches(live_capture, captured)
        with authenticated_material_session(material_stage, captured) as frozen_session:
            replayed = compare_frozen_original(frozen_session, run_id=run_id, as_of=as_of,
                expected=original, captured=captured, limits=limits)
        self._sources(captured)
        if material_path.read_bytes() != raw:
            raise ValueError("publication_bundle_material_original_changed")
        self._require_full_source_closure(captured)
        bundle = frozen_bundle_from_verified_read(original, replayed,
            material_ref="materials/" + sha(raw) + ".json", material_sha256=sha(raw), material_bytes=len(raw))
        prepared = PreparedFrozenPublication(uuid4().hex, bundle)
        self._issued[prepared.nonce] = (prepared, material_path, sha(raw), captured)
        return prepared

    def capture_and_prepare(self, original_session: Session, *, run_id: str, as_of: datetime,
                            material_path: Path, material_stage: Path,
                            sources: tuple[SourceFile, ...], limits: Limits,
                            max_material_bytes: int) -> PreparedFrozenPublication:
        """Worker/backfill path: one live full original read, one frozen replay.

        The retained registry/installation is already configured by the trusted
        product launcher. SourceFile inputs come from its actual validated
        receipt/index closure, not from end-user report attributes.
        """
        if type(max_material_bytes) is not int or max_material_bytes < 1:
            raise ValueError("publication_bundle_explicit_material_bound_exceeded")
        with NativeCollector(original_session, limits=limits, trace_slot_owned=True) as collection:
            original = read_registered_original(original_session, run_id, as_of=as_of)
            collection.collect_sources(self.root, sources)
        captured = collection.snapshot()
        if captured["observed_queries_status"] != "complete":
            raise ValueError("publication_bundle_original_dependency_unresolved")
        raw = canonical(captured)
        if len(raw) > max_material_bytes:
            raise ValueError("publication_bundle_explicit_material_bound_exceeded")
        self._sources(captured)
        material_path.parent.mkdir(parents=True, exist_ok=True)
        with material_path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        verify_material_file(material_path, expected_sha256=sha(raw),
            expected_bytes=len(raw), max_bytes=max_material_bytes)
        with authenticated_material_session(material_stage, captured) as frozen_session:
            replayed = compare_frozen_original(frozen_session, run_id=run_id, as_of=as_of,
                expected=original, captured=captured, limits=limits)
        self._sources(captured)
        if material_path.read_bytes() != raw:
            raise ValueError("publication_bundle_material_original_changed")
        self._require_full_source_closure(captured)
        bundle = frozen_bundle_from_verified_read(original, replayed,
            material_ref="materials/" + sha(raw) + ".json", material_sha256=sha(raw), material_bytes=len(raw))
        prepared = PreparedFrozenPublication(uuid4().hex, bundle)
        self._issued[prepared.nonce] = (prepared, material_path, sha(raw), captured)
        return prepared

    def verify(self, prepared: PreparedFrozenPublication) -> None:
        entry = self._issued.get(prepared.nonce)
        if entry is None or entry[0] is not prepared:
            raise ValueError("publication_bundle_preparation_issuer_changed")
        _, material_path, digest, captured = entry
        verify_material_file(material_path, expected_sha256=digest,
            expected_bytes=prepared.value.material_bytes, max_bytes=prepared.value.material_bytes)
        self._sources(captured)
        path = Path(original_reader.__file__).resolve(strict=True)
        if sha(path.read_bytes()) != prepared.value.trusted_reader_source_sha256:
            raise ValueError("publication_bundle_registered_reader_changed")

    def material_source(self, prepared: PreparedFrozenPublication) -> Path:
        entry = self._issued.get(prepared.nonce)
        if entry is None or entry[0] is not prepared:
            raise ValueError("publication_bundle_preparation_issuer_changed")
        return entry[1]  # Internal issued staging path; no end-user reference.

    def consume(self, prepared: PreparedFrozenPublication) -> None:
        self.verify(prepared)
        self._issued.pop(prepared.nonce)
