"""Trusted installed shadow execution and original-only component qualification.

This reader never promotes a component from a reported pass flag. Stored path
V1/V2 keep their original protocols. Composition supplies explicit source,
feature, monitor-rule and research-recipe original handlers; absent handlers
remain unavailable. Local pure execution costs do not qualify financial costs.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.component_registry import _read
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import (
    REPORT,
    REPORT_V2,
    checked_payload,
)
from packages.kernel.decision_hub_kernel.decision.path_forecast import (
    LEGACY_PARAMETERS,
    PARAMETERS,
)
from packages.kernel.decision_hub_kernel.decision.path_forecast_score import (
    score_path_forecasts,
    verify_observation,
)
from packages.kernel.decision_hub_kernel.decision.path_score_inference import MatchedEventBlock
from packages.kernel.decision_hub_kernel.persistence.db import as_utc, utcnow
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
    MarketComponentShadowRecord,
    MarketComponentVersionRecord,
)
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskPathObservationRecord,
    RiskShadowTrialRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_protocol import (
    ComponentExecutionUsage,
    ComponentOriginalRef,
    ComponentProtocolContext,
    ComponentProtocolResult,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import (
    ComponentManifest,
    ComponentQualification,
    ShadowTrial,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash
from packages.kernel.decision_hub_kernel.ports.path_forecast import (
    PathObservationReceipt,
    RiskPathForecast,
    parse_path_parameters,
)

from .market_components import MarketComponents, _json

PATH_PROTOCOL = {
    "schema_version": "component-path-originals-protocol.v1",
    "report_version": REPORT,
    "score_version": "path-shadow-score.v1",
    "parameters": LEGACY_PARAMETERS.model_dump(mode="json"),
    "comparison": "registered_BASE_EMPIRICAL_V1_vs_COND_VOL_V1_same_inputs",
    "minimum_time_blocks": 100,
    "ci": "not_registered",
    "matching_event_blocks": "not_registered",
    "cost": "actual_usage_originals_required_unavailable",
    "promotion_allowed": False,
}
PATH_PROTOCOL_HASH = canonical_hash(PATH_PROTOCOL)
PATH_PROTOCOL_V2 = {
    **PATH_PROTOCOL,
    "schema_version": "component-path-originals-protocol.v2",
    "report_version": REPORT_V2,
    "score_version": "path-shadow-score.v2",
    "parameters": PARAMETERS.model_dump(mode="json"),
    "ci": PARAMETERS.ci_protocol,
    "matching_event_blocks": "authenticated-primary-cross-asset-clusters.v1",
}
PATH_PROTOCOL_V2_HASH = canonical_hash(PATH_PROTOCOL_V2)

_LOCAL_CALLBACK_SCOPE = ContextVar("market_component_local_callback_scope", default=None)


def _deny_callback_acquisition(event, args):
    scope = _LOCAL_CALLBACK_SCOPE.get()
    if scope is not None and (
        event.startswith("socket.")
        or event in {"subprocess.Popen", "os.system", "os.fork", "os.posix_spawn"}
    ):
        scope["denied_external_attempts"] += 1
        raise ValueError("component_local_callback_external_acquisition_forbidden")


sys.addaudithook(_deny_callback_acquisition)


def _local_callback(item, payload, parameters):
    scope = {"denied_external_attempts": 0}
    token = _LOCAL_CALLBACK_SCOPE.set(scope)
    try:
        output = _json(item.callback(_json(payload), _json(parameters)))
    finally:
        _LOCAL_CALLBACK_SCOPE.reset(token)
    if scope["denied_external_attempts"]:
        raise ValueError("component_local_callback_external_acquisition_forbidden")
    return output


class ProductionComponentOriginalReader:
    def __init__(
        self,
        database,
        installed,
        raw_archive,
        *,
        path_ledger=None,
        clock=utcnow,
        protocol_handlers=(),
    ):
        self.database, self.raw_archive, self.path_ledger, self.clock = (
            database,
            raw_archive,
            path_ledger,
            clock,
        )
        versions = {item.version_id: item for item in installed}
        if len(versions) != len(installed):
            raise ValueError("component_original_installed_version_duplicate")
        self.installed = MappingProxyType(versions)
        bindings = {}
        for handler in protocol_handlers:
            if handler.kind in bindings or handler.kind == "model":
                raise ValueError("component_protocol_handler_kind_invalid")
            function = handler.evaluate.__func__
            file = Path(inspect.getsourcefile(function)).resolve()
            bindings[handler.kind] = (
                handler,
                function,
                function.__code__,
                file,
                hashlib.sha256(file.read_bytes()).hexdigest(),
                handler.protocol_id,
                handler.protocol_hash,
            )
        self.protocol_handlers = MappingProxyType(bindings)

    def _protocol_handler(self, manifest, trial):
        binding = self.protocol_handlers.get(manifest.kind)
        if binding is None:
            raise ValueError("component_protocol_handler_unavailable")
        handler, function, code, file, digest, protocol_id, protocol_hash = binding
        if (
            handler.evaluate.__func__ is not function
            or function.__code__ is not code
            or hashlib.sha256(file.read_bytes()).hexdigest() != digest
            or (handler.protocol_id, handler.protocol_hash) != (protocol_id, protocol_hash)
            or trial.protocol_hash != protocol_hash
        ):
            raise ValueError("component_protocol_handler_identity_changed")
        return handler, canonical_hash(
            {
                "protocol_id": protocol_id,
                "protocol_hash": protocol_hash,
                "source_sha256": digest,
            }
        )

    def _protocol_captures(self, session, manifest, trial, as_of):
        captures = []
        rows = session.scalars(
            select(MarketComponentAuditRecord)
            .where(
                MarketComponentAuditRecord.pointer_id == self._pointer(trial.trial_id),
                MarketComponentAuditRecord.created_at <= as_of,
            )
            .order_by(MarketComponentAuditRecord.audit_id)
            .limit(513)
            .execution_options(populate_existing=True)
        ).all()
        if len(rows) > 512:
            raise ValueError("component_shadow_original_census_bound_exceeded")
        for row in rows:
            body = self._audit(row, trial)
            if body["schema_version"] not in (
                "market-component-shadow-execution.v1",
                "market-component-shadow-execution.v2",
            ):
                continue
            intent = body["intent"]
            known = aware_utc(datetime.fromisoformat(body["known_at"]))
            cutoff = aware_utc(datetime.fromisoformat(intent["cutoff_at"]))
            source_known = aware_utc(datetime.fromisoformat(intent["source_known_at"]))
            if (
                not trial.start_at <= cutoff < trial.end_at
                or not source_known <= cutoff <= known <= as_of
            ):
                raise ValueError("component_protocol_capture_clock_invalid")
            payload = self._raw(intent["input_ref"], intent["input_hash"])
            item = self._installed(manifest)
            output = _local_callback(item, payload, dict(manifest.parameters))
            if (canonical_hash(payload), output, canonical_hash(output)) != (
                body["input_hash"],
                body["output"],
                body["output_hash"],
            ):
                raise ValueError("component_shadow_callback_replay_changed")
            captures.append(body)
        return tuple(captures)

    def _protocol_result(self, session, manifest, trial, refs, as_of):
        connection = session.connection()
        native = connection.connection.driver_connection
        count = native.total_changes
        handler, identity = self._protocol_handler(manifest, trial)
        captures = self._protocol_captures(session, manifest, trial, as_of)
        with session.no_autoflush:
            result = handler.evaluate(
                session,
                manifest=manifest,
                trial=trial,
                captures=captures,
                original_refs=refs,
                as_of=as_of,
                context=ComponentProtocolContext(
                    self.raw_archive,
                    self.path_ledger,
                    self.installed,
                ),
            )
        result = ComponentProtocolResult.model_validate(result.model_dump(mode="json"))
        if (
            (result.protocol_id, result.protocol_hash) != (handler.protocol_id, trial.protocol_hash)
            or result.known_at > as_of
            or result.known_at < trial.registered_at
            or not set(refs).issubset(set(result.original_refs))
            or session.connection() is not connection
            or not native.in_transaction
            or native.total_changes != count
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_protocol_result_binding_invalid")
        self._protocol_handler(manifest, trial)
        return result, identity

    def record_component_evaluation_in_session(
        self, session, *, request_id, trial_id, original_refs
    ):
        """Exact original refs are re-read; only the caller's physical writer persists audit."""
        MarketComponents._snapshot(session)
        if not request_id or len(request_id) > 210:
            raise ValueError("component_protocol_request_id_invalid")
        refs = tuple(ComponentOriginalRef.model_validate(raw) for raw in original_refs)
        if len(refs) > 128 or len({r.original_ref for r in refs}) != len(refs):
            raise ValueError("component_protocol_original_census_invalid")
        trial = _read(session.get(MarketComponentShadowRecord, trial_id), ShadowTrial)
        manifest = _read(
            session.get(MarketComponentVersionRecord, trial.version_id), ComponentManifest
        )
        self._installed(manifest)
        now = aware_utc(self.clock())
        key = "component-protocol-evaluation:" + request_id
        intent = {"original_refs": [r.model_dump(mode="json") for r in refs]}

        def build():
            result, identity = self._protocol_result(session, manifest, trial, refs, now)
            return {
                "schema_version": "market-component-protocol-evaluation.v1",
                "audit_id": "cshadoweval_" + canonical_hash(key)[:40],
                "request_id": key,
                "trial_id": trial_id,
                "manifest_hash": manifest.payload_hash,
                "protocol_hash": trial.protocol_hash,
                "known_at": now.isoformat(),
                "intent": intent,
                "handler_identity_hash": identity,
                "result": result.model_dump(mode="json"),
                "trading_authority": False,
            }

        body = self._capture(session, key, trial, manifest, intent, build)
        at = aware_utc(datetime.fromisoformat(body["known_at"]))
        result, identity = self._protocol_result(session, manifest, trial, refs, at)
        if (
            body["result"] != result.model_dump(mode="json")
            or body["handler_identity_hash"] != identity
        ):
            raise ValueError("component_protocol_evaluation_writer_changed")
        return body

    def _installed(self, manifest):
        item = self.installed.get(manifest.version_id)
        if item is None:
            raise ValueError("component_shadow_version_not_installed")
        item.verify()
        if item.manifest(parent_version_id=manifest.parent_version_id) != manifest:
            raise ValueError("component_shadow_installed_manifest_changed")
        return item

    @staticmethod
    def _pointer(trial_id):
        return canonical_hash(["component-shadow-originals", trial_id])

    def _raw(self, ref, digest):
        body = self.raw_archive.read(ref)
        if len(body) > 1024 * 1024 or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("component_shadow_raw_original_changed")
        return json.loads(body)

    @classmethod
    def _audit(cls, row, trial):
        if row is None:
            raise ValueError("component_shadow_original_missing")
        body = json.loads(row.payload_json)
        if (
            canonical_hash(body) != row.payload_hash
            or row.audit_id != body["audit_id"]
            or row.request_id != body["request_id"]
            or row.pointer_id != cls._pointer(trial.trial_id)
            or as_utc(row.created_at) != aware_utc(datetime.fromisoformat(body["known_at"]))
            or body["trial_id"] != trial.trial_id
            or body["manifest_hash"] != trial.manifest_hash
            or body["protocol_hash"] != trial.protocol_hash
            or body["trading_authority"] is not False
        ):
            raise ValueError("component_shadow_audit_original_invalid")
        return body

    def _capture(self, session, request_id, trial, manifest, intent, build):
        connection, native = MarketComponents._snapshot(session)
        count = native.total_changes
        existing = session.scalar(
            select(MarketComponentAuditRecord).where(
                MarketComponentAuditRecord.request_id == request_id
            )
        )
        if existing is not None:
            body = self._audit(existing, trial)
            if body["intent"] != intent:
                raise ValueError("component_shadow_request_reused")
            return body
        body = build()
        self._installed(manifest)
        if (
            session.connection() is not connection
            or not native.in_transaction
            or native.total_changes != count
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_shadow_capture_scope_changed")
        row = MarketComponentAuditRecord(
            audit_id=body["audit_id"],
            request_id=request_id,
            pointer_id=self._pointer(trial.trial_id),
            created_at=datetime.fromisoformat(body["known_at"]),
            payload_json=json.dumps(body, sort_keys=True, allow_nan=False),
            payload_hash=canonical_hash(body),
        )
        session.add(row)
        session.flush()
        fresh = session.get(MarketComponentAuditRecord, row.audit_id, populate_existing=True)
        if self._audit(fresh, trial) != body:
            raise ValueError("component_shadow_capture_writer_changed")
        if (
            _read(
                session.get(MarketComponentShadowRecord, trial.trial_id, populate_existing=True),
                ShadowTrial,
            )
            != trial
        ):
            raise ValueError("component_shadow_trial_writer_changed")
        if (
            _read(
                session.get(
                    MarketComponentVersionRecord, manifest.version_id, populate_existing=True
                ),
                ComponentManifest,
            )
            != manifest
        ):
            raise ValueError("component_shadow_manifest_writer_changed")
        self._installed(manifest)
        return body

    def record_shadow_in_session(
        self, session, *, request_id, trial_id, input_ref, input_hash, source_known_at, cutoff_at
    ):
        """Actual manual shadow callback, without requiring an active pointer.

        This is a trusted in-process port. The source caller must authenticate
        input lineage/first-known before providing the exact archived input.
        No self-reported output, approval, scope or numerical score is accepted.
        """
        MarketComponents._snapshot(session)
        now, cutoff, source_known = map(aware_utc, (self.clock(), cutoff_at, source_known_at))
        trial = _read(session.get(MarketComponentShadowRecord, trial_id), ShadowTrial)
        manifest = _read(
            session.get(MarketComponentVersionRecord, trial.version_id), ComponentManifest
        )
        if (
            trial.manifest_hash != manifest.payload_hash
            or not trial.start_at <= cutoff < trial.end_at
        ):
            raise ValueError("component_shadow_trial_scope_invalid")
        if not source_known <= cutoff <= now or now >= trial.end_at:
            raise ValueError("component_shadow_capture_clock_invalid")
        if not request_id or len(request_id) > 220:
            raise ValueError("component_shadow_request_id_invalid")
        key = "component-shadow:" + request_id
        intent = {
            "input_ref": input_ref,
            "input_hash": input_hash,
            "source_known_at": source_known.isoformat(),
            "cutoff_at": cutoff.isoformat(),
        }
        item = self._installed(manifest)

        def build():
            payload = self._raw(input_ref, input_hash)
            usage = None
            if manifest.kind in self.protocol_handlers:
                started = aware_utc(self.clock())
                elapsed_start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
                output = _local_callback(item, payload, dict(item.parameters))
                elapsed, cpu = (
                    time.perf_counter_ns() - elapsed_start,
                    time.thread_time_ns() - cpu_start,
                )
                completed = aware_utc(self.clock())
                usage = ComponentExecutionUsage(
                    started_at=started,
                    completed_at=completed,
                    elapsed_ns=elapsed,
                    thread_cpu_ns=cpu,
                    external_calls=0,
                    boundary="network-and-process-denied-installed-pure-callback.v1",
                    manifest_hash=manifest.payload_hash,
                    input_hash=canonical_hash(payload),
                    output_hash=canonical_hash(output),
                )
            else:
                output = _json(item.callback(_json(payload), _json(dict(item.parameters))))
            if manifest.kind == "model":
                forecast = RiskPathForecast.model_validate(output)
                if (forecast.scope.scope_id, forecast.cutoff_at) != (manifest.scope_id, cutoff):
                    raise ValueError("component_shadow_prediction_scope_invalid")
                if forecast.published_at > now or forecast.published_at < source_known:
                    raise ValueError("component_shadow_prediction_clock_invalid")
            body = {
                "schema_version": "market-component-shadow-execution.v2"
                if usage
                else ("market-component-shadow-execution.v1"),
                "audit_id": "cshadow_" + canonical_hash(key)[:40],
                "request_id": key,
                "trial_id": trial_id,
                "manifest_hash": manifest.payload_hash,
                "protocol_hash": trial.protocol_hash,
                "known_at": usage.completed_at.isoformat() if usage else now.isoformat(),
                "intent": intent,
                "input_hash": canonical_hash(payload),
                "output": output,
                "output_hash": canonical_hash(output),
                "probability_qualified": False,
                "trading_authority": False,
            }
            if usage is not None:
                body["execution_usage"] = usage.model_dump(mode="json")
            return body

        value = self._capture(session, key, trial, manifest, intent, build)
        if canonical_hash(self._raw(input_ref, input_hash)) != value["input_hash"]:
            raise ValueError("component_shadow_input_writer_changed")
        return value

    def record_evaluation_in_session(self, session, *, request_id, trial_id, report_id):
        """Attach one exact genuine path report; never select latest or invent a score."""
        MarketComponents._snapshot(session)
        if not request_id or len(request_id) > 220:
            raise ValueError("component_shadow_request_id_invalid")
        trial = _read(session.get(MarketComponentShadowRecord, trial_id), ShadowTrial)
        manifest = _read(
            session.get(MarketComponentVersionRecord, trial.version_id), ComponentManifest
        )
        self._installed(manifest)
        if (
            manifest.kind != "model"
            or trial.protocol_hash not in (PATH_PROTOCOL_HASH, PATH_PROTOCOL_V2_HASH)
            or self.path_ledger is None
        ):
            raise ValueError("component_shadow_protocol_unsupported")
        report, _, _ = self._path_report(report_id, manifest, trial)
        now = aware_utc(self.clock())
        if datetime.fromisoformat(report["computed_at"]) > now:
            raise ValueError("component_shadow_report_future")
        key = "component-shadow-evaluation:" + request_id
        intent = {"report_id": report_id, "report_hash": report["payload_hash"]}

        def build():
            return {
                "schema_version": "market-component-shadow-evaluation.v1",
                "audit_id": "cshadoweval_" + canonical_hash(key)[:40],
                "request_id": key,
                "trial_id": trial_id,
                "manifest_hash": manifest.payload_hash,
                "protocol_hash": trial.protocol_hash,
                "known_at": now.isoformat(),
                "intent": intent,
                "trading_authority": False,
            }

        value = self._capture(session, key, trial, manifest, intent, build)
        fresh, _, _ = self._path_report(report_id, manifest, trial)
        if fresh != report:
            raise ValueError("component_shadow_report_writer_changed")
        return value

    def _path_report(self, report_id, manifest, trial):
        if self.path_ledger is None:
            raise ValueError("component_shadow_path_original_reader_unavailable")
        report = self.path_ledger.verified_report(report_id)
        with self.database.session() as session:
            row = session.get(RiskShadowTrialRecord, report["trial_id"])
            if row is None:
                raise ValueError("component_shadow_path_trial_missing")
            protocol = self.path_ledger.protocol(row)
            parameters = parse_path_parameters(protocol["parameters"])
            report_version, bridge_hash = (
                (REPORT, PATH_PROTOCOL_HASH)
                if parameters.protocol_version == "path-shadow-24h.v1"
                else (REPORT_V2, PATH_PROTOCOL_V2_HASH)
            )
            first = as_utc(row.first_cutoff_at)
            registered = as_utc(row.registered_at)
            if (
                report["protocol_hash"] != row.protocol_hash
                or report["report_version"] != report_version
                or trial.protocol_hash != bridge_hash
            ):
                raise ValueError("component_shadow_path_report_protocol_invalid")
        scopes = tuple(item["scope_id"] for item in protocol["scopes"])
        if (
            manifest.scope_id not in scopes
            or registered < trial.registered_at
            or not (
                trial.start_at <= first
                and first + timedelta(days=parameters.shadow_days) <= trial.end_at
            )
        ):
            raise ValueError("component_shadow_path_trial_binding_invalid")
        slots = tuple(
            (scope, first + timedelta(days=day))
            for scope in scopes
            for day in range(parameters.shadow_days)
        )
        forecasts = tuple(
            RiskPathForecast.model_validate(
                self.path_ledger.prediction(ref["prediction_id"])["forecast"]
            )
            for ref in report["prediction_refs"]
        )
        labels = []
        for ref in report["observation_refs"]:
            if ref["complete"]:
                label = self.path_ledger.verified_observation(ref["observation_id"])
            else:
                with self.database.session() as session:
                    row = session.get(RiskPathObservationRecord, ref["observation_id"])
                    if row is None or row.payload_hash != ref["payload_hash"]:
                        raise ValueError("component_shadow_partial_label_original_changed")
                    label = PathObservationReceipt.model_validate(
                        checked_payload(row.payload_json, row.payload_hash)
                    )
                    if (label.scope.scope_id, label.input_cutoff_at, label.label_known_at) != (
                        row.scope_id,
                        as_utc(row.cutoff_at),
                        as_utc(row.label_known_at),
                    ):
                        raise ValueError("component_shadow_partial_label_identity_changed")
                verify_observation(label)
                self.path_ledger.inputs.verify_observation_originals(label)
            labels.append(label)
        labels = tuple(labels)
        computed = aware_utc(datetime.fromisoformat(report["computed_at"]))
        if computed > aware_utc(self.clock()):
            raise ValueError("component_shadow_report_future")
        alarm_score = self.path_ledger.read_report_alarm_score(report)
        event_blocks = (
            None
            if alarm_score is None
            else tuple(
                MatchedEventBlock(
                    block_id=raw["block_id"],
                    cutoff_at=aware_utc(datetime.fromisoformat(raw["cutoff_at"])),
                    known_at=aware_utc(datetime.fromisoformat(raw["known_at"])),
                    original_ref=raw["original_ref"],
                    original_hash=raw["original_hash"],
                    scope_ids=tuple(raw["scope_ids"]),
                )
                for raw in alarm_score["matched_event_blocks"]
            )
        )
        score = score_path_forecasts(
            forecasts,
            labels,
            expected_slots=slots,
            as_of=computed,
            parameters=parameters,
            event_blocks=event_blocks,
            alarm_score=alarm_score,
        )
        if score != report["score"]:
            raise ValueError("component_shadow_score_recomputation_failed")
        return report, forecasts, labels

    def verify(self, manifest, trial):
        self._installed(manifest)
        refs, hashes, captures, evaluations = [], [], [], []
        now = aware_utc(self.clock())
        known = trial.registered_at
        with self.database.session() as session:
            connection = session.connection()
            prior_query_only = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
            connection.exec_driver_sql("BEGIN")
            connection.exec_driver_sql("PRAGMA query_only=ON")
            try:
                if (
                    _read(
                        session.get(MarketComponentVersionRecord, manifest.version_id),
                        ComponentManifest,
                    )
                    != manifest
                    or _read(session.get(MarketComponentShadowRecord, trial.trial_id), ShadowTrial)
                    != trial
                ):
                    raise ValueError("component_shadow_review_original_changed")
                rows = session.scalars(
                    select(MarketComponentAuditRecord)
                    .where(MarketComponentAuditRecord.pointer_id == self._pointer(trial.trial_id))
                    .order_by(MarketComponentAuditRecord.audit_id)
                    .limit(513)
                ).all()
                if len(rows) > 512:
                    raise ValueError("component_shadow_original_census_bound_exceeded")
                for row in rows:
                    body = self._audit(row, trial)
                    clock = aware_utc(datetime.fromisoformat(body["known_at"]))
                    if clock > now:
                        raise ValueError("component_shadow_original_future")
                    known = max(known, clock)
                    refs.append("market_component_audit:" + row.audit_id)
                    hashes.append(row.payload_hash)
                    if body["schema_version"] in (
                        "market-component-shadow-execution.v1",
                        "market-component-shadow-execution.v2",
                    ):
                        intent = body["intent"]
                        cutoff = aware_utc(datetime.fromisoformat(intent["cutoff_at"]))
                        if (
                            not trial.start_at <= cutoff < trial.end_at
                            or not aware_utc(datetime.fromisoformat(intent["source_known_at"]))
                            <= cutoff
                            <= clock
                        ):
                            raise ValueError("component_shadow_input_clock_invalid")
                        payload = self._raw(intent["input_ref"], intent["input_hash"])
                        output = (
                            _local_callback(
                                self.installed[manifest.version_id], payload, manifest.parameters
                            )
                            if body["schema_version"] == "market-component-shadow-execution.v2"
                            else (
                                _json(
                                    self.installed[manifest.version_id].callback(
                                        _json(payload), _json(manifest.parameters)
                                    )
                                )
                            )
                        )
                        if (
                            canonical_hash(payload) != body["input_hash"]
                            or output != body["output"]
                            or canonical_hash(output) != body["output_hash"]
                        ):
                            raise ValueError("component_shadow_callback_replay_changed")
                        refs.append(intent["input_ref"])
                        hashes.append(intent["input_hash"])
                        captures.append(body)
                    elif body["schema_version"] in (
                        "market-component-shadow-evaluation.v1",
                        "market-component-protocol-evaluation.v1",
                    ):
                        evaluations.append(body)
                    else:
                        raise ValueError("component_shadow_original_schema_invalid")
            finally:
                # Restore the exact borrowed physical connection before rollback
                # returns it to the pool. Reacquiring afterward may pick another.
                connection.exec_driver_sql(f"PRAGMA query_only={int(prior_query_only)}")
                session.rollback()
        self._installed(manifest)
        reasons = []
        blocks = 0
        if manifest.kind in self.protocol_handlers:
            if len(evaluations) != 1 or evaluations[0]["schema_version"] != (
                "market-component-protocol-evaluation.v1"
            ):
                return ComponentQualification(
                    trial_id=trial.trial_id,
                    manifest_hash=manifest.payload_hash,
                    protocol_hash=trial.protocol_hash,
                    original_refs=tuple(refs),
                    original_hashes=tuple(hashes),
                    known_at=known,
                    status="insufficient",
                    reasons=("component_protocol_evaluation_originals_incomplete",),
                )
            body = evaluations[0]
            refs_input = tuple(
                ComponentOriginalRef.model_validate(raw) for raw in body["intent"]["original_refs"]
            )
            at = aware_utc(datetime.fromisoformat(body["known_at"]))
            if at > now:
                raise ValueError("component_protocol_evaluation_future")
            with self.database.session() as session:
                connection = session.connection()
                prior = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
                connection.exec_driver_sql("BEGIN")
                connection.exec_driver_sql("PRAGMA query_only=ON")
                try:
                    result, identity = self._protocol_result(
                        session, manifest, trial, refs_input, at
                    )
                finally:
                    connection.exec_driver_sql(f"PRAGMA query_only={int(prior)}")
                    session.rollback()
            if (
                result.model_dump(mode="json") != body["result"]
                or identity != body["handler_identity_hash"]
            ):
                raise ValueError("component_protocol_evaluation_original_changed")
            return ComponentQualification(
                trial_id=trial.trial_id,
                manifest_hash=manifest.payload_hash,
                protocol_hash=trial.protocol_hash,
                original_refs=tuple(r.original_ref for r in result.original_refs)
                + (body["audit_id"],),
                original_hashes=tuple(r.original_hash for r in result.original_refs)
                + (canonical_hash(body),),
                known_at=max(at, result.known_at),
                status=result.status,
                reasons=result.reasons,
                independent_blocks=result.independent_blocks,
                sample_qualified=result.sample_qualified,
                cost_verified=result.cost_verified,
            )
        if manifest.kind != "model" or trial.protocol_hash not in (
            PATH_PROTOCOL_HASH,
            PATH_PROTOCOL_V2_HASH,
        ):
            reasons.append("component_shadow_protocol_unsupported")
        elif not captures or not evaluations:
            reasons.append("component_shadow_originals_incomplete")
        else:
            if len(evaluations) != 1:
                raise ValueError("component_shadow_evaluation_ambiguous")
            report, forecasts, labels = self._path_report(
                evaluations[0]["intent"]["report_id"], manifest, trial
            )
            if report["payload_hash"] != evaluations[0]["intent"]["report_hash"]:
                raise ValueError("component_shadow_report_original_changed")
            candidate_outputs = {
                canonical_hash(f.model_dump(mode="json"))
                for f in forecasts
                if f.scope.scope_id == manifest.scope_id
                and f.model_version == manifest.component_ref
            }
            recorded_outputs = {body["output_hash"] for body in captures}
            if not candidate_outputs or candidate_outputs != recorded_outputs:
                reasons.append("component_shadow_real_consumption_census_incomplete")
            refs.extend(
                ["risk_evaluation_report:" + report["report_id"]]
                + [
                    "risk_path_observation:" + ref["observation_id"]
                    for ref in report["observation_refs"]
                ]
            )
            hashes.extend([report["payload_hash"]] + [label.payload_hash for label in labels])
            known = max(
                known,
                aware_utc(datetime.fromisoformat(report["computed_at"])),
                *(label.label_known_at for label in labels),
            )
            blocks = report["score"]["independent_paired_day_blocks"]
            reasons.extend(report["score"]["reasons"])
            reasons.append("component_shadow_actual_usage_cost_originals_unavailable")
        # Engineering CI/matching support is independent of future sample and
        # complete actual cost qualification. A self-reported pass never suffices.
        status = (
            "unavailable"
            if not refs or any("unsupported" in r or "incomplete" in r for r in reasons)
            else "insufficient"
        )
        return ComponentQualification(
            trial_id=trial.trial_id,
            manifest_hash=manifest.payload_hash,
            protocol_hash=trial.protocol_hash,
            original_refs=tuple(refs),
            original_hashes=tuple(hashes),
            known_at=known,
            status=status,
            reasons=tuple(dict.fromkeys(reasons)),
            independent_blocks=blocks,
            sample_qualified=False,
            cost_verified=False,
        )
