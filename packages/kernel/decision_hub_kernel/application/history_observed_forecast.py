"""Authenticated independent forecast prepare, immutable writer and read-only reader."""

from __future__ import annotations

import ast
import base64
import contextlib
import dataclasses
import hashlib
import hmac
import inspect
import math
import secrets
import sys
import threading
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from types import CodeType, ModuleType

from sqlalchemy.orm import Session

from ..persistence import history_observed_forecast_records as record_module
from ..persistence.db import as_utc
from ..persistence.history_observed_forecast_records import HistoryObservedForecastRecord as Record
from ..persistence.history_observed_records import HistoryObservedTrainingRecord as ModelRecord
from ..ports import history_observed_forecast as port_module
from ..ports.history_observed_forecast import HistoryForecastIdentityV1, RecordedHistoryForecastV1
from ..ports.history_observed_model import (
    FrozenHistoryObservedPathModelV2,
    HistoryObservedProspectiveForecastV1,
)
from ..ports.history_observed_training import seal, verify_payload
from ..ports.market_path import MarketCandle, canonical_hash
from .history_observed_commands import HistoryObservedCommandService
from .history_observed_dataset import (
    HistoryObservedPreparation,
    encoded,
    require_snapshot,
    snapshot_identity,
)
from .history_observed_model import HistoryObservedModelService

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CONSUMER_FILES = (
    "packages/kernel/decision_hub_kernel/ports/history_observed_forecast.py",
    "packages/kernel/decision_hub_kernel/application/history_observed_forecast.py",
    "packages/kernel/decision_hub_kernel/persistence/history_observed_forecast_records.py",
    "packages/kernel/decision_hub_kernel/application/history_observed_commands.py",
)
DEPENDENCY_MODULES = (
    "ports.history_observed_training",
    "ports.history_observed_model",
    "ports.market_path",
    "ports.path_forecast",
    "application.history_observed_dataset",
    "application.history_observed_ledger",
    "application.history_observed_model",
    "application.history_observed_commands",
    "application.history_observed_acquisition",
    "persistence.db",
    "persistence.history_observed_records",
    "persistence.risk_records",
    "decision.path_risk",
)


def _function(value: object) -> object:
    return value.__func__ if isinstance(value, (classmethod, staticmethod)) else value


def _bindings(modules: tuple[ModuleType, ...]) -> dict[tuple[str, str, str], object]:
    found: dict[tuple[str, str, str], object] = {}
    for module in modules:
        for name, value in vars(module).items():
            owner = getattr(value, "__module__", "")
            if (inspect.isfunction(value) or inspect.isclass(value)) and owner.startswith(
                "packages."
            ):
                found[module.__name__, name, ""] = value
                wrapped = getattr(value, "__wrapped__", None)
                if inspect.isfunction(value) and inspect.isfunction(wrapped):
                    found[module.__name__, name, "::__wrapped__"] = wrapped
                if inspect.isclass(value) and value.__module__ == module.__name__:
                    for child, member in vars(value).items():
                        if isinstance(member, property):
                            found[module.__name__, name, child + "::descriptor"] = member
                            for accessor in ("fget", "fset", "fdel"):
                                function = getattr(member, accessor)
                                if inspect.isfunction(function):
                                    found[module.__name__, name, child + "::" + accessor] = function
                        function = _function(member)
                        if inspect.isfunction(function):
                            found[module.__name__, name, child] = function
                            wrapped = getattr(function, "__wrapped__", None)
                            if inspect.isfunction(wrapped):
                                found[module.__name__, name, child + "::__wrapped__"] = wrapped
    return found


def _codes(code: CodeType) -> dict[str, list[CodeType]]:
    result: dict[str, list[CodeType]] = {}

    def visit(current: CodeType) -> None:
        result.setdefault(current.co_qualname, []).append(current)
        for item in current.co_consts:
            if isinstance(item, CodeType):
                visit(item)

    visit(code)
    return result


class _LoadedContract:
    """Compare loaded callables to fresh compiled source without executing that source."""

    def __init__(self, modules: tuple[ModuleType, ...]) -> None:
        self.modules = modules
        self.bindings = _bindings(modules)
        self.loaded_codes = {
            key: value.__code__ for key, value in self.bindings.items() if inspect.isfunction(value)
        }
        self.binding_reader = _bindings
        self.code_reader = _codes

    def validate(self) -> None:
        if _bindings is not self.binding_reader or _codes is not self.code_reader:
            raise ValueError("history_forecast_loaded_guard_changed")
        current = self.binding_reader(self.modules)
        if current.keys() != self.bindings.keys() or any(
            current[key] is not value for key, value in self.bindings.items()
        ):
            raise ValueError("history_forecast_loaded_binding_changed")
        if any(current[key].__code__ is not code for key, code in self.loaded_codes.items()):
            raise ValueError("history_forecast_loaded_code_changed")
        for module in self.modules:
            path = Path(module.__file__ or "")
            source = path.read_bytes()
            own = [
                (key, value)
                for key, value in self.bindings.items()
                if key[0] == module.__name__
                and inspect.isfunction(value)
                and value.__module__ == module.__name__
            ]
            # Imported aliases are identity-bound above; each defining module is
            # separately compiled here. Generated ORM/Pydantic runtime code is
            # not misrepresented as source-defined methods.
            compiled = self.code_reader(
                compile(
                    source,
                    str(path),
                    "exec",
                    dont_inherit=True,
                    optimize=sys.flags.optimize,
                )
            )
            overload_lines = {
                min(node.lineno, *(decorator.lineno for decorator in node.decorator_list))
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef)
                and any(
                    isinstance(decorator, ast.Name) and decorator.id == "overload"
                    for decorator in node.decorator_list
                )
            }
            for key, function in own:
                if (
                    function.__code__.co_filename == contextlib.__file__
                    and not key[2].endswith("::__wrapped__")
                    and (key[0], key[1], key[2] + "::__wrapped__") in self.bindings
                ):
                    # The sealed standard contextmanager wrapper delegates to
                    # the separately compiled source-defined generator.
                    continue
                candidates = tuple(
                    code
                    for code in compiled.get(function.__qualname__, ())
                    if code.co_firstlineno not in overload_lines
                )
                generated_name = key[2].split("::", 1)[0]
                owner = vars(module).get(key[1])
                if (
                    not candidates
                    and generated_name == "__repr__"
                    and hasattr(owner, "__dataclass_fields__")
                    and function.__code__.co_filename == dataclasses.__file__
                    and (key[0], key[1], "__repr__::__wrapped__") in self.bindings
                ):
                    continue
                if (
                    not candidates
                    and generated_name
                    in {
                        "__init__",
                        "__repr__",
                        "__eq__",
                        "__hash__",
                        "__setattr__",
                        "__delattr__",
                    }
                    and function.__code__.co_filename == "<string>"
                ):
                    # Explicit dataclass/SQLAlchemy generated runtime methods
                    # still have their function and code identities sealed.
                    continue
                if (
                    len(candidates) != 1
                    or Path(function.__code__.co_filename).resolve() != path.resolve()
                    or function.__code__ != candidates[0]
                ):
                    raise ValueError("history_forecast_loaded_code_changed")


def forecast_id(identity: HistoryForecastIdentityV1) -> str:
    return (
        "hobs_forecast_" + canonical_hash([identity.operator_owner_claim, identity.request_id])[:48]
    )


@dataclass(frozen=True)
class HistoryForecastPreparation:
    identity: HistoryForecastIdentityV1
    forecast: HistoryObservedProspectiveForecastV1
    selected_closes: tuple[MarketCandle, ...]
    protocol_hash: str
    model_original_ref: str
    model_original_hash: str
    original_model_sources: tuple[tuple[str, str], ...]
    consumer_implementation_files: tuple[tuple[str, str], ...]
    consumer_source_original_ref: str
    consumer_source_original_hash: str
    dataset_preparation: HistoryObservedPreparation
    original_readset: tuple[tuple[str, str, str], ...]
    issuer: object
    thread_id: int
    _auth: bytes = field(repr=False)


class HistoryObservedForecastService:
    def __init__(self, model: HistoryObservedModelService) -> None:
        if type(model) is not HistoryObservedModelService:
            raise ValueError("history_forecast_original_model_service_required")
        self.model = model
        self.ledger = model.ledger
        self.archive = self.ledger.builder.archive
        self._key = secrets.token_bytes(32)
        self._root = self.ledger.root
        self._instances = self._dependencies()
        self._callees = self._called_functions()
        self._injected_codes = tuple(
            (function, getattr(function, "__code__", None))
            for value in (
                self.ledger.clock,
                self.ledger.builder.clock,
                self.ledger.builder.originals.raw_reader,
            )
            if (function := getattr(value, "__func__", value)) is not None
        )
        modules = tuple(
            sys.modules["packages.kernel.decision_hub_kernel." + name]
            for name in DEPENDENCY_MODULES
        ) + (port_module, sys.modules[__name__], record_module)
        modules += (sys.modules["packages.provider_adapters.research.raw_capture"],)
        self._loaded = _LoadedContract(modules)
        self._sealed_loaded = self._loaded

    def _dependencies(self) -> tuple[object, ...]:
        builder = self.ledger.builder
        return (
            self.model,
            self.model.ledger,
            self.ledger,
            builder,
            builder.originals,
            builder.archive,
            self.archive,
            self.ledger.clock,
            builder.clock,
            builder.originals.raw_reader,
        )

    def _called_functions(self) -> tuple[object, ...]:
        originals = self.ledger.builder.originals
        return tuple(
            (getattr(value, "__self__", None), getattr(value, "__func__", value))
            for value in (
                self.model.forecast,
                self.model.model,
                self.ledger.protocol,
                self.ledger.require_sources,
                originals.load,
                self.archive.read,
                self.archive.put,
                self.archive.reference,
            )
        )

    def _sources(
        self, expected: tuple[tuple[str, str], ...] | None = None
    ) -> tuple[tuple[tuple[str, str], ...], dict[str, object]]:
        if (
            self._loaded is not self._sealed_loaded
            or any(a is not b for a, b in zip(self._instances, self._dependencies(), strict=True))
            or self._callees != self._called_functions()
            or self.ledger.root != self._root
            or any(
                getattr(function, "__code__", None) is not code
                for function, code in self._injected_codes
            )
        ):
            raise ValueError("history_forecast_dependency_binding_changed")
        self._loaded.validate()
        payloads = tuple((path, (self._root / path).read_bytes()) for path in CONSUMER_FILES)
        sources = tuple((path, hashlib.sha256(body).hexdigest()) for path, body in payloads)
        loaded = (
            port_module.LOADED_SOURCE_SHA256,
            LOADED_SOURCE_SHA256,
            record_module.LOADED_SOURCE_SHA256,
        )
        if tuple(digest for _, digest in sources[:3]) != loaded or (
            expected is not None and expected != sources
        ):
            raise ValueError("history_forecast_consumer_source_changed_or_unknown_ABI")
        return sources, {
            "schema_version": "history-forecast-consumer-sources.v1",
            "implementation_files": sources,
            "source_bytes_base64": [
                (path, base64.b64encode(body).decode("ascii")) for path, body in payloads
            ],
        }

    def _preparation_body(self, prepared: HistoryForecastPreparation) -> bytes:
        return encoded(
            {
                "identity": prepared.identity.model_dump(mode="json"),
                "forecast": prepared.forecast.model_dump(mode="json"),
                "selected_closes": [
                    bar.model_dump(mode="json") for bar in prepared.selected_closes
                ],
                "protocol_hash": prepared.protocol_hash,
                "model_original_ref": prepared.model_original_ref,
                "model_original_hash": prepared.model_original_hash,
                "original_model_sources": prepared.original_model_sources,
                "consumer_implementation_files": prepared.consumer_implementation_files,
                "consumer_source_original_ref": prepared.consumer_source_original_ref,
                "consumer_source_original_hash": prepared.consumer_source_original_hash,
                "dataset_preparation_hash": prepared.dataset_preparation.payload_hash,
                "dataset_preparation_auth": prepared.dataset_preparation._auth.hex(),
                "dataset_preparation_issuer": id(prepared.dataset_preparation.producer),
                "dataset_preparation_thread": prepared.dataset_preparation.thread_id,
                "original_readset": prepared.original_readset,
                "thread_id": prepared.thread_id,
                "issuer": id(self),
            }
        )

    def _recheck(
        self,
        session: Session,
        identity: HistoryForecastIdentityV1,
        forecast: HistoryObservedProspectiveForecastV1,
        *,
        authenticated_model: FrozenHistoryObservedPathModelV2 | None = None,
    ) -> tuple[tuple[MarketCandle, ...], FrozenHistoryObservedPathModelV2, ModelRecord]:
        verify_payload(forecast)
        if (
            identity.cutoff_at > self.model.now()
            or (
                forecast.registration_id,
                forecast.scope,
                forecast.model_hash,
                forecast.model_known_at,
                forecast.cutoff_at,
                forecast.threshold,
            )
            != (
                identity.registration_id,
                identity.scope,
                identity.expected_model_hash,
                identity.expected_model_known_at,
                identity.cutoff_at,
                identity.threshold,
            )
            or forecast.produced_at > self.model.now()
        ):
            raise ValueError("history_forecast_identity_or_future_changed")
        model = authenticated_model or self.model.model(
            session,
            identity.registration_id,
            identity.scope,
        )
        if (model.payload_hash, model.model_known_at) != (
            identity.expected_model_hash,
            identity.expected_model_known_at,
        ):
            raise ValueError("history_forecast_expected_model_changed")
        model_record_id = (
            "hobs_model_" + canonical_hash([identity.registration_id, identity.scope.scope_id])[:48]
        )
        row = session.get(ModelRecord, model_record_id, populate_existing=True)
        if row is None:
            raise ValueError("history_forecast_original_model_missing")
        bars, source = self.ledger.builder.originals.load(
            session,
            identity.scope,
            identity.cutoff_at,
            identity.cutoff_at,
        )
        body = encoded(source)
        if self.archive.read(forecast.feature_original_ref, forecast.feature_original_hash) != body:
            raise ValueError("history_forecast_feature_readset_changed")
        closed = {bar.close_at: bar for bar in bars if bar.close_at <= identity.cutoff_at}
        latest = max(closed, default=None)
        if latest is None or not 0 <= (identity.cutoff_at - latest).total_seconds() <= 180:
            raise ValueError("history_forecast_recent_31_closes_required")
        times = tuple(latest - timedelta(minutes=index) for index in range(30, -1, -1))
        if any(stamp not in closed for stamp in times):
            raise ValueError("history_forecast_recent_31_closes_required")
        selected = tuple(closed[stamp] for stamp in times)
        with localcontext() as context:
            context.prec, context.rounding = 28, ROUND_HALF_EVEN
            rv = Decimal(
                str(
                    math.sqrt(
                        sum(
                            math.log(float(right.close / left.close)) ** 2
                            for left, right in zip(selected, selected[1:], strict=False)
                        )
                    )
                )
            )
            group = "low" if rv <= model.fit.training_vol_median else "high"
            support = model.fit.conditional_low if group == "low" else model.fit.conditional_high
            threshold = Decimal(identity.threshold)

            def probability(values: tuple[Decimal, ...]) -> Decimal:
                return (Decimal(sum(value >= threshold for value in values)) + Decimal("0.5")) / (
                    Decimal(len(values)) + 1
                )

            numbers = (
                probability(model.fit.baseline.long_mae_sorted),
                probability(model.fit.baseline.short_mae_sorted),
                probability(support.long_mae_sorted),
                probability(support.short_mae_sorted),
            )
        if (
            forecast.realized_vol_30m,
            forecast.selected_group,
            forecast.baseline_n,
            forecast.conditional_n,
            forecast.feature_known_at,
            forecast.latest_close_at,
            forecast.baseline_long_probability,
            forecast.baseline_short_probability,
            forecast.conditional_long_probability,
            forecast.conditional_short_probability,
        ) != (
            rv,
            group,
            model.fit.baseline.n,
            support.n,
            max(bar.known_at for bar in selected),
            latest,
            *numbers,
        ):
            raise ValueError("history_forecast_numbers_or_feature_changed")
        return selected, model, row

    def _original_readset(
        self,
        session: Session,
        identity: HistoryForecastIdentityV1,
        forecast: HistoryObservedProspectiveForecastV1,
        model: FrozenHistoryObservedPathModelV2,
        consumer_ref: str,
        consumer_hash: str,
    ) -> tuple[tuple[str, str, str], ...]:
        import json

        refs: set[tuple[str, str, str]] = {
            ("archive", forecast.feature_original_ref, forecast.feature_original_hash),
            ("archive", consumer_ref, consumer_hash),
            (
                "archive",
                model.fit.admission.readset_original_ref,
                model.fit.admission.readset_original_hash,
            ),
        }
        for record_id in (
            identity.registration_id,
            "hobs_dataset_"
            + canonical_hash([identity.registration_id, identity.scope.scope_id])[:48],
            "hobs_model_"
            + canonical_hash([identity.registration_id, identity.scope.scope_id])[:48],
        ):
            row = session.get(ModelRecord, record_id, populate_existing=True)
            if row is None:
                raise ValueError("history_forecast_model_readset_missing")
            refs.add(("archive", row.original_ref, row.original_hash))
            payload = json.loads(self.archive.read(row.original_ref, row.original_hash))
            for window in payload.get("windows", ()):
                refs.add(("archive", window["source_manifest_ref"], window["source_manifest_hash"]))
        for _, ref, digest in tuple(refs):
            payload = json.loads(self.archive.read(ref, digest))
            captures = payload.get("retained_capture_originals", ())
            if payload.get("schema_version") == "history-observed-admission-readset.v1":
                captures = payload.get("captures", ())
            for capture in captures:
                refs.add(("raw", capture["body_ref"], capture["body_hash"]))
        result = tuple(sorted(refs))
        self._confirm_original_readset(result)
        return result

    def _confirm_original_readset(self, refs: tuple[tuple[str, str, str], ...]) -> None:
        if not 1 <= len(refs) <= 2048:
            raise ValueError("history_forecast_original_readset_invalid")
        for kind, ref, digest in refs:
            if kind == "archive":
                self.archive.read(ref, digest)
            elif kind == "raw":
                body = self.ledger.builder.originals.raw_reader(ref)
                if hashlib.sha256(body).hexdigest() != digest:
                    raise ValueError("history_forecast_original_readset_changed")
            else:
                raise ValueError("history_forecast_original_readset_invalid")

    def prepare_history_forecast(
        self,
        session: Session,
        identity: HistoryForecastIdentityV1,
    ) -> HistoryForecastPreparation | RecordedHistoryForecastV1:
        boundary = snapshot_identity(session)
        identity = HistoryForecastIdentityV1.model_validate_json(identity.model_dump_json())
        sources, source_body = self._sources()
        existing = session.get(Record, forecast_id(identity), populate_existing=True)
        if existing is not None:
            value = self.read_history_forecast(session, identity)
            require_snapshot(session, boundary)
            return value
        protocol = self.ledger.protocol(session, identity.registration_id)
        forecast = self.model.forecast(
            session,
            identity.registration_id,
            identity.scope,
            identity.cutoff_at,
            threshold=identity.threshold,
        )
        dataset_preparation = self.ledger.prepare_saved(
            session,
            identity.registration_id,
            identity.scope,
        )
        model = self.model._saved_model(
            session,
            protocol,
            dataset_preparation.dataset,
            metadata_only=True,
        )
        selected, model, row = self._recheck(
            session,
            identity,
            forecast,
            authenticated_model=model,
        )
        ref, digest = self.archive.put(source_body)
        readset = self._original_readset(session, identity, forecast, model, ref, digest)
        draft = HistoryForecastPreparation(
            identity,
            forecast,
            selected,
            protocol.payload_hash,
            row.original_ref,
            row.original_hash,
            model.fit.admission.implementation_files,
            sources,
            ref,
            digest,
            dataset_preparation,
            readset,
            self,
            threading.get_ident(),
            b"",
        )
        auth = hmac.digest(self._key, self._preparation_body(draft), "sha256")
        self._sources(sources)
        self.ledger.require_sources(protocol)
        require_snapshot(session, boundary)
        self._confirm_original_readset(readset)
        return replace(draft, _auth=auth)

    def record_history_forecast(
        self,
        session: Session,
        prepared: HistoryForecastPreparation,
    ) -> RecordedHistoryForecastV1:
        boundary = snapshot_identity(session)
        if (
            prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not hmac.compare_digest(
                prepared._auth, hmac.digest(self._key, self._preparation_body(prepared), "sha256")
            )
        ):
            raise ValueError("history_forecast_preparation_changed")
        sources, source_body = self._sources(prepared.consumer_implementation_files)
        if self.archive.read(
            prepared.consumer_source_original_ref,
            prepared.consumer_source_original_hash,
        ) != encoded(source_body):
            raise ValueError("history_forecast_consumer_original_changed")
        protocol = self.ledger.protocol(session, prepared.identity.registration_id)
        self.ledger.confirm_saved(session, prepared.dataset_preparation)
        model = self.model._saved_model(
            session,
            protocol,
            prepared.dataset_preparation.dataset,
            metadata_only=True,
        )
        selected, model, row = self._recheck(
            session,
            prepared.identity,
            prepared.forecast,
            authenticated_model=model,
        )
        if (
            self._original_readset(
                session,
                prepared.identity,
                prepared.forecast,
                model,
                prepared.consumer_source_original_ref,
                prepared.consumer_source_original_hash,
            )
            != prepared.original_readset
        ):
            raise ValueError("history_forecast_prepared_readset_changed")
        if (
            selected != prepared.selected_closes
            or protocol.payload_hash != prepared.protocol_hash
            or (row.original_ref, row.original_hash)
            != (
                prepared.model_original_ref,
                prepared.model_original_hash,
            )
            or model.fit.admission.implementation_files != prepared.original_model_sources
        ):
            raise ValueError("history_forecast_prepared_readset_changed")
        existing = session.get(Record, forecast_id(prepared.identity), populate_existing=True)
        if existing is not None:
            value = self.read_history_forecast(session, prepared.identity)
            if value.forecast.feature_original_hash != prepared.forecast.feature_original_hash:
                raise ValueError("history_forecast_request_conflict")
            require_snapshot(session, boundary)
            self._confirm_original_readset(prepared.original_readset)
            return value
        value = seal(
            RecordedHistoryForecastV1,
            forecast_id=forecast_id(prepared.identity),
            identity=prepared.identity,
            protocol_hash=prepared.protocol_hash,
            model_original_ref=prepared.model_original_ref,
            model_original_hash=prepared.model_original_hash,
            original_model_sources=prepared.original_model_sources,
            consumer_implementation_files=sources,
            consumer_source_original_ref=prepared.consumer_source_original_ref,
            consumer_source_original_hash=prepared.consumer_source_original_hash,
            selected_closes=selected,
            original_readset=prepared.original_readset,
            forecast=prepared.forecast,
            recorded_at=self.model.now(),
        )
        ref, digest = self.archive.put(value.model_dump(mode="json"))
        self._sources(sources)
        self.ledger.require_sources(protocol)
        require_snapshot(session, boundary, before_write=True)
        session.add(
            Record(
                forecast_id=value.forecast_id,
                operator_owner_claim=value.identity.operator_owner_claim,
                request_id=value.identity.request_id,
                registration_id=value.identity.registration_id,
                scope_id=value.identity.scope.scope_id,
                model_hash=value.identity.expected_model_hash,
                model_known_at=value.identity.expected_model_known_at,
                cutoff_at=value.identity.cutoff_at,
                threshold=value.identity.threshold,
                payload_json=encoded(value.model_dump(mode="json")).decode(),
                payload_hash=value.payload_hash,
                original_ref=ref,
                original_hash=digest,
                produced_at=value.forecast.produced_at,
                recorded_at=value.recorded_at,
            )
        )
        session.flush()
        self._sources(sources)
        self.ledger.require_sources(protocol)
        self._confirm_original_readset(prepared.original_readset)
        if self.archive.read(ref, digest) != encoded(value.model_dump(mode="json")):
            raise ValueError("history_forecast_original_changed_at_exit")
        require_snapshot(session, (*boundary[:4], boundary[4] + 1, *boundary[5:]))
        return value

    def read_history_forecast(
        self,
        session: Session,
        identity: HistoryForecastIdentityV1,
    ) -> RecordedHistoryForecastV1:
        boundary = snapshot_identity(session)
        identity = HistoryForecastIdentityV1.model_validate_json(identity.model_dump_json())
        self._sources()
        row = session.get(Record, forecast_id(identity), populate_existing=True)
        if row is None:
            raise ValueError("history_forecast_saved_record_missing")
        body = self.archive.read(row.original_ref, row.original_hash)
        value = verify_payload(RecordedHistoryForecastV1.model_validate_json(body))
        if value.identity != identity:
            raise ValueError("history_forecast_request_conflict")
        if (
            row.forecast_id,
            row.operator_owner_claim,
            row.request_id,
            row.registration_id,
            row.scope_id,
            row.model_hash,
            as_utc(row.model_known_at),
            as_utc(row.cutoff_at),
            row.threshold,
            row.payload_json.encode(),
            row.payload_hash,
            as_utc(row.produced_at),
            as_utc(row.recorded_at),
        ) != (
            forecast_id(identity),
            identity.operator_owner_claim,
            identity.request_id,
            identity.registration_id,
            identity.scope.scope_id,
            identity.expected_model_hash,
            identity.expected_model_known_at,
            identity.cutoff_at,
            identity.threshold,
            body,
            value.payload_hash,
            value.forecast.produced_at,
            value.recorded_at,
        ) or value.recorded_at > self.model.now():
            raise ValueError("history_forecast_saved_row_or_clock_changed")
        sources, consumer_body = self._sources(value.consumer_implementation_files)
        if self.archive.read(
            value.consumer_source_original_ref,
            value.consumer_source_original_hash,
        ) != encoded(consumer_body):
            raise ValueError("history_forecast_consumer_original_changed")
        selected, model, model_row = self._recheck(session, identity, value.forecast)
        protocol = self.ledger.protocol(session, identity.registration_id)
        if (
            self._original_readset(
                session,
                identity,
                value.forecast,
                model,
                value.consumer_source_original_ref,
                value.consumer_source_original_hash,
            )
            != value.original_readset
        ):
            raise ValueError("history_forecast_saved_readset_changed")
        if (
            selected != value.selected_closes
            or protocol.payload_hash != value.protocol_hash
            or (model_row.original_ref, model_row.original_hash)
            != (
                value.model_original_ref,
                value.model_original_hash,
            )
            or model.fit.admission.implementation_files != value.original_model_sources
        ):
            raise ValueError("history_forecast_saved_readset_changed")
        self._sources(sources)
        self.ledger.require_sources(protocol)
        self._confirm_original_readset(value.original_readset)
        if self.archive.read(row.original_ref, row.original_hash) != body:
            raise ValueError("history_forecast_original_changed_at_exit")
        require_snapshot(session, boundary)
        return value


class HistoryForecastCommittedReadbackError(ValueError):
    """The write returned from COMMIT; its follow-up reader did not confirm it."""


class HistoryForecastLocalCommands:
    """Existing physical transaction wrappers; return only after successful COMMIT."""

    def __init__(self, history_commands: HistoryObservedCommandService) -> None:
        if type(history_commands) is not HistoryObservedCommandService or (
            history_commands.model.ledger is not history_commands.ledger
            or history_commands.acquisition.ledger is not history_commands.ledger
            or history_commands.acquisition.database is not history_commands.database
        ):
            raise ValueError("history_forecast_command_composition_invalid")
        self.history_commands = history_commands
        self.forecasts = HistoryObservedForecastService(history_commands.model)
        self._command_instances = (
            history_commands,
            history_commands.database,
            history_commands.acquisition,
        )
        self._boundaries = self._boundary_functions()

    def _boundary_functions(self) -> tuple[object, ...]:
        history = self.history_commands
        return tuple(
            (getattr(value, "__self__", None), getattr(value, "__func__", value))
            for value in (
                history._read_session,
                history._write_session,
                history.acquisition._writer,
                history.database.session,
            )
        )

    def _require_command(self) -> None:
        history = self.history_commands
        if (
            any(
                a is not b
                for a, b in zip(
                    self._command_instances,
                    (history, history.database, history.acquisition),
                    strict=True,
                )
            )
            or self._boundaries != self._boundary_functions()
        ):
            raise ValueError("history_forecast_command_boundary_changed")
        self.forecasts._sources()

    def record(self, identity: HistoryForecastIdentityV1) -> RecordedHistoryForecastV1:
        self._require_command()
        with self.history_commands._read_session() as session:
            prepared = self.forecasts.prepare_history_forecast(session, identity)
        with self.history_commands._write_session() as session:
            if isinstance(prepared, RecordedHistoryForecastV1):
                value = self.forecasts.read_history_forecast(session, identity)
            else:
                value = self.forecasts.record_history_forecast(session, prepared)
        try:
            confirmed = self.read(identity)
            if confirmed != value:
                raise ValueError("history_forecast_commit_readback_changed")
        except Exception as error:
            raise HistoryForecastCommittedReadbackError(
                "history_forecast_committed_readback_unconfirmed"
            ) from error
        return confirmed

    def read(self, identity: HistoryForecastIdentityV1) -> RecordedHistoryForecastV1:
        self._require_command()
        with self.history_commands._read_session() as session:
            return self.forecasts.read_history_forecast(session, identity)
