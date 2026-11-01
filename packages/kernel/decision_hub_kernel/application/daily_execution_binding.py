"""UNWIRED STAGE DRAFT. Not imported, executed, tested, or an attestation.

Proposed final path: application/daily_execution_binding.py.
Only this module's actual invocation produces an in-process witness. A saved
JSON declaration does not supply that witness. Existing late results/cost are
retained, while cancellation prevents creation of a new authoritative role.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import hashlib
import inspect
import json
import math
import platform
import re
import sqlite3
import statistics
import sys
import types
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    _fact_model,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    CapabilityAdapterReturn,
)
from packages.kernel.decision_hub_kernel.decision import daily_strategy as strategy
from packages.kernel.decision_hub_kernel.persistence.db import (
    DataJobRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchToolCallReservationRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

SCHEMA = "daily-execution.v1"
ROLE_PREFIX = "daily_execution:"
DATASET = "crypto.btc_usdt.ohlcv_1d"
ATTRIBUTE = "daily_execution_receipt_json"
_ROOT = Path(__file__).resolve().parents[4]  # Correct only at proposed final path.
_CONTEXT = re.compile(r"historical:(datajob_[a-f0-9]{32}):([1-9][0-9]*):(.+)\Z")
_SUPPORTED_FILES = {
    "packages/kernel/decision_hub_kernel/decision/daily_strategy.py",
    "packages/kernel/decision_hub_kernel/application/daily_execution_binding.py",
    "packages/provider_adapters/historical/gate_daily.py",
    "packages/provider_adapters/historical/research.py",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("daily_execution_timezone_required")
    return value.astimezone(UTC)


def _stored(value: datetime | None) -> datetime:
    result = as_utc(value)
    if result is None:
        raise ValueError("daily_execution_stored_time_missing")
    return result


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _daily_asset(dataset: str) -> tuple[str, str, str]:
    """Finite source-checked mapping; unknown datasets gain no authority."""
    if dataset == "crypto.btc_usdt.ohlcv_1d":
        return "BTC_USDT", "BTC-USDT", "usdt_and_btc"
    if dataset == "crypto.eth_usdt.ohlcv_1d":
        return "ETH_USDT", "ETH-USDT", "usdt_and_eth"
    raise ValueError("daily_execution_dataset_unsupported")


class DailyLoadedIdentity(_Strict):
    schema_version: Literal["daily-loaded-identity.v1"] = "daily-loaded-identity.v1"
    source_hashes: dict[str, str]
    callable_code_hashes: dict[str, str]
    defaults_hashes: dict[str, str]
    runtime: dict[str, Any]


class DailyExecutionReceipt(_Strict):
    schema_version: Literal["daily-execution.v1"] = "daily-execution.v1"
    run_id: str
    request_id: str
    research_session_id: str
    generation: int = Field(ge=1)
    reservation_query_hash: str
    adapter_query_json: str
    adapter_query_hash: str
    job_id: str
    job_request_hash: str
    capture_id: str
    capture_row_hash: str
    body_hash: str
    body_ref: str
    query_start_at: datetime
    query_end_at: datetime
    closed_at: datetime
    normalized_rows_hash: str
    strategy_input_hash: str
    summary_json: str
    strategy_receipt_json: str
    computed_started_at: datetime
    computed_completed_at: datetime
    loaded_identity: DailyLoadedIdentity
    stable_recipe_hash: str

    @field_validator(
        "query_start_at",
        "query_end_at",
        "closed_at",
        "computed_started_at",
        "computed_completed_at",
    )
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return _time(value)

    @field_validator(
        "reservation_query_hash",
        "adapter_query_hash",
        "job_request_hash",
        "capture_row_hash",
        "body_hash",
        "normalized_rows_hash",
        "strategy_input_hash",
        "stable_recipe_hash",
    )
    @classmethod
    def _digest(cls, value: str) -> str:
        if re.fullmatch(r"[a-f0-9]{64}", value) is None:
            raise ValueError("daily_execution_digest_invalid")
        return value


@dataclass(frozen=True)
class OriginalDailyInput:
    job_id: str
    job_request_hash: str
    capture_id: str
    capture_row_hash: str
    body_hash: str
    body_ref: str
    body: bytes
    job_start_at: datetime
    job_end_at: datetime
    closed_at: datetime


@dataclass
class _ComputationCollector:
    run_id: str
    outer_query_json: str
    reservation_query_hash: str
    expected_lease_owner: str | None
    token: object = field(default_factory=object)
    active: bool = True
    witness: ExecutedDailyComputation | None = None


_CURRENT: ContextVar[_ComputationCollector | None] = ContextVar(
    "daily_actual_computation",
    default=None,
)


@contextmanager
def daily_computation_scope(
    *,
    run_id: str,
    query: ResearchCapabilityQuery,
    reservation_query_hash: str,
    expected_lease_owner: str | None,
) -> Generator[_ComputationCollector]:
    """Gateway-owned, no Session/HTTP/model, valid only during actual adapter entry."""
    collector = _ComputationCollector(
        run_id,
        _canonical(query.model_dump(mode="json")),
        reservation_query_hash,
        expected_lease_owner,
    )
    token = _CURRENT.set(collector)
    try:
        yield collector
    finally:
        collector.active = False
        _CURRENT.reset(token)


def _atom(value: Any) -> Any:
    """Canonical code/default values; unknown executable objects fail closed."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return {"float_hex": value.hex()}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, (tuple, list)):
        return [_atom(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {"set": sorted((_atom(item) for item in value), key=_canonical)}
    if isinstance(value, dict):
        return {str(key): _atom(item) for key, item in sorted(value.items())}
    if isinstance(value, types.CodeType):
        return _code(value)
    if isinstance(value, types.FunctionType):
        return {
            "python_function": [value.__module__, value.__qualname__],
            "code": _code(value.__code__),
        }
    if isinstance(
        value,
        (
            types.BuiltinFunctionType,
            types.BuiltinMethodType,
            types.MethodDescriptorType,
            types.WrapperDescriptorType,
        ),
    ):
        return {
            "native_callable": [
                getattr(value, "__module__", None),
                value.__qualname__,
                type(value).__name__,
            ],
        }
    if isinstance(value, type):
        return {"runtime_type": [value.__module__, value.__qualname__]}
    raise ValueError("daily_loaded_default_unsupported")


def _code(value: types.CodeType) -> dict[str, Any]:
    # Locations vary across worktrees; interpreter/code shape do not.
    return {
        "argcount": value.co_argcount,
        "posonlyargcount": value.co_posonlyargcount,
        "kwonlyargcount": value.co_kwonlyargcount,
        "nlocals": value.co_nlocals,
        "stacksize": value.co_stacksize,
        "flags": value.co_flags,
        "code": value.co_code.hex(),
        "exceptiontable": value.co_exceptiontable.hex(),
        "consts": [_atom(item) for item in value.co_consts],
        "names": list(value.co_names),
        "varnames": list(value.co_varnames),
        "freevars": list(value.co_freevars),
        "cellvars": list(value.co_cellvars),
    }


def _find_code(value: types.CodeType, qualname: str) -> types.CodeType:
    found: list[types.CodeType] = []

    def visit(code: types.CodeType) -> None:
        if code.co_qualname == qualname:
            found.append(code)
        for item in code.co_consts:
            if isinstance(item, types.CodeType):
                visit(item)

    visit(value)
    if len(found) != 1:
        raise ValueError("daily_loaded_source_callable_not_unique")
    return found[0]


def _function(value: Any) -> types.FunctionType:
    if inspect.ismethod(value):
        value = value.__func__
    if not isinstance(value, types.FunctionType):
        raise ValueError("daily_loaded_callable_not_python_function")
    if value.__closure__:
        # Core source functions have no nonlocal cells. Dataclass constructors
        # are checked separately against a generated runtime template below.
        raise ValueError("daily_loaded_callable_closure_unsupported")
    return value


def _check_function(
    role: str,
    callable_value: Any,
    *,
    source_hashes: dict[str, str],
    code_hashes: dict[str, str],
    defaults_hashes: dict[str, str],
    visited: set[int],
    compiled: dict[Path, types.CodeType],
    recursive: bool = True,
) -> None:
    actual = _function(callable_value)
    if id(actual) in visited:
        return
    visited.add(id(actual))
    module = sys.modules.get(actual.__module__)
    filename = getattr(module, "__file__", None)
    if not filename:
        raise ValueError("daily_loaded_module_source_missing")
    path = Path(filename).resolve()
    body = path.read_bytes()
    if path.is_relative_to(_ROOT):
        relative = path.relative_to(_ROOT).as_posix()
        if relative not in _SUPPORTED_FILES:
            raise ValueError("daily_loaded_project_dependency_not_declared")
    else:
        # Only the standard JSON/statistics closure is allowed outside this repo.
        if not actual.__module__.startswith(("json", "statistics")):
            raise ValueError("daily_loaded_external_dependency_unsupported")
        relative = "stdlib/" + actual.__module__.replace(".", "/") + ".py"
    source_hashes[relative] = _sha(body)
    expected_module = compiled.setdefault(
        path,
        compile(body, relative, "exec", dont_inherit=True, optimize=sys.flags.optimize),
    )
    expected = _find_code(expected_module, actual.__qualname__)
    if _code(actual.__code__) != _code(expected):
        raise ValueError("daily_loaded_callable_differs_from_source")
    code_hashes[role] = _sha(_canonical(_code(actual.__code__)))
    defaults_hashes[role] = _sha(
        _canonical(
            {
                "defaults": _atom(actual.__defaults__),
                "kwdefaults": _atom(actual.__kwdefaults__),
            }
        )
    )
    # Imported Python callees such as statistics._ss/_sum are actually loaded
    # functions too. Native C dependencies are separately pinned in runtime.
    for name in actual.__code__.co_names if recursive else ():
        dependency = actual.__globals__.get(name)
        if isinstance(dependency, types.FunctionType):
            _check_function(
                role + "/global:" + name,
                dependency,
                source_hashes=source_hashes,
                code_hashes=code_hashes,
                defaults_hashes=defaults_hashes,
                visited=visited,
                compiled=compiled,
            )
        elif isinstance(
            dependency,
            (
                types.BuiltinFunctionType,
                types.BuiltinMethodType,
                types.MethodDescriptorType,
            ),
        ):
            # A Python callee's actual imported C alias is part of its runtime
            # identity. Rebinding fmean's fsum cannot keep the original recipe.
            defaults_hashes[role + "/native-global:" + name] = _sha(
                _canonical(_atom(dependency)),
            )


def _check_generated_init(cls: type, *, code_hashes: dict[str, str]) -> None:
    """Check the actual dataclass constructor, rather than calling its disk SHA proof."""
    if not dataclasses.is_dataclass(cls):
        raise ValueError("daily_loaded_dataclass_missing")
    parameters = cls.__dataclass_params__  # pyright: ignore[reportAttributeAccessIssue]
    if not parameters.frozen or not parameters.init or not parameters.eq:
        raise ValueError("daily_loaded_dataclass_shape_changed")
    fields = dataclasses.fields(cls)
    source_path = Path(inspect.getfile(cls)).resolve()
    source_tree = ast.parse(source_path.read_bytes())
    declarations = [
        item
        for item in source_tree.body
        if isinstance(item, ast.ClassDef) and item.name == cls.__name__
    ]
    if len(declarations) != 1:
        raise ValueError("daily_loaded_dataclass_source_missing")
    source_fields = [
        item
        for item in declarations[0].body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    ]
    if [item.name for item in fields] != [
        item.target.id for item in source_fields if isinstance(item.target, ast.Name)
    ]:
        raise ValueError("daily_loaded_dataclass_source_fields_changed")
    for actual_field, source_field in zip(fields, source_fields, strict=True):
        if actual_field.type != ast.unparse(source_field.annotation):
            raise ValueError("daily_loaded_dataclass_source_annotation_changed")
        source_default = (
            dataclasses.MISSING
            if source_field.value is None
            else ast.literal_eval(source_field.value)
        )
        if actual_field.default is not source_default and actual_field.default != source_default:
            raise ValueError("daily_loaded_dataclass_source_default_changed")
    expected_fields = []
    for item in fields:
        if item.default_factory is not dataclasses.MISSING or not item.init or item.kw_only:
            raise ValueError("daily_loaded_dataclass_field_unsupported")
        expected_fields.append(
            (item.name, item.type)
            if item.default is dataclasses.MISSING
            else (item.name, item.type, dataclasses.field(default=item.default))
        )
    template = dataclasses.make_dataclass(cls.__name__, expected_fields, frozen=True)
    actual = cls.__dict__.get("__init__")
    expected = template.__dict__.get("__init__")
    if not isinstance(actual, types.FunctionType) or not isinstance(expected, types.FunctionType):
        raise ValueError("daily_loaded_dataclass_constructor_missing")
    if _code(actual.__code__) != _code(expected.__code__):
        raise ValueError("daily_loaded_dataclass_constructor_changed")
    if _atom(actual.__defaults__) != _atom(expected.__defaults__):
        raise ValueError("daily_loaded_dataclass_defaults_changed")
    # Frozen dataclass init's only permitted cell is actual builtins.object.
    if any(cell.cell_contents is not builtins.object for cell in actual.__closure__ or ()):
        raise ValueError("daily_loaded_dataclass_constructor_closure_changed")
    code_hashes[cls.__name__ + ".__init__"] = _sha(_canonical(_code(actual.__code__)))


@dataclass(frozen=True)
class LoadedDailyImplementation:
    """Exact trusted producer callables, not an arbitrary declared identity."""

    normalizer: Callable[..., list[dict[str, object]]]
    row_projector: Callable[[Sequence[Mapping[str, object]]], list[dict[str, object]]]
    summary: Callable[[str, list[dict[str, object]]], dict[str, str]]
    numeric: Callable[[object], Decimal]
    formatter: Callable[[Decimal], str]
    timestamp: Callable[[object], datetime | None]
    units: Callable[[str, str, str], str]

    def identity(self) -> DailyLoadedIdentity:
        functions = {
            "compute": strategy.compute_daily_strategy,
            "bar_validate": strategy.DailyBar.from_mapping,
            "bar_values": strategy.DailyBar.numeric_values.fget,
            "receipt_dict": strategy.DailyStrategyReceipt.as_dict,
            "receipt_json": strategy.DailyStrategyReceipt.as_json,
            "normalizer": self.normalizer,
            "row_projector": self.row_projector,
            "summary": self.summary,
            "numeric": self.numeric,
            "formatter": self.formatter,
            "timestamp": self.timestamp,
            "units": self.units,
            "json_dumps": json.dumps,
            "json_loads": json.loads,
            "json_encode": json.JSONEncoder.encode,
            "json_iterencode": json.JSONEncoder.iterencode,
            "json_decoder_init": json.JSONDecoder.__init__,
            "json_decode": json.JSONDecoder.decode,
            "json_raw_decode": json.JSONDecoder.raw_decode,
            "fmean": statistics.fmean,
            "pstdev": statistics.pstdev,
        }
        # Explicit relationships stop a replaced global alias from hiding behind
        # a genuine source function and somebody else's correct file hashes.
        expected_globals = (
            (strategy.compute_daily_strategy, "DailyBar", strategy.DailyBar),
            (
                strategy.compute_daily_strategy,
                "DailyStrategyReceipt",
                strategy.DailyStrategyReceipt,
            ),
            (strategy.compute_daily_strategy, "fmean", statistics.fmean),
            (strategy.compute_daily_strategy, "pstdev", statistics.pstdev),
            (strategy.compute_daily_strategy, "math", math),
            (strategy.compute_daily_strategy, "json", json),
            (strategy.DailyStrategyReceipt.as_json, "json", json),
            (self.normalizer, "DailyBar", strategy.DailyBar),
            (self.normalizer, "datetime", datetime),
            (self.normalizer, "timedelta", timedelta),
            (self.normalizer, "UTC", UTC),
            (self.row_projector, "_timestamp", self.timestamp),
            (self.summary, "_decimal", self.numeric),
            (self.summary, "_format", self.formatter),
            (self.summary, "Decimal", Decimal),
            (self.numeric, "Decimal", Decimal),
            (self.numeric, "InvalidOperation", InvalidOperation),
            (self.timestamp, "datetime", datetime),
            (json.dumps, "JSONEncoder", json.JSONEncoder),
            (json.loads, "JSONDecoder", json.JSONDecoder),
            (statistics.fmean, "fsum", math.fsum),
            (read_original_daily_input_in_session, "_daily_asset", _daily_asset),
            (verify_daily_computation_in_session, "_daily_asset", _daily_asset),
        )
        for function, name, expected in expected_globals:
            if _function(function).__globals__.get(name) is not expected:
                raise ValueError("daily_loaded_callee_binding_changed")
        sources: dict[str, str] = {}
        codes: dict[str, str] = {}
        defaults: dict[str, str] = {}
        visited: set[int] = set()
        compiled: dict[Path, types.CodeType] = {}
        for role, function in functions.items():
            _check_function(
                role,
                function,
                source_hashes=sources,
                code_hashes=codes,
                defaults_hashes=defaults,
                visited=visited,
                compiled=compiled,
            )
        # The authority/writer closure is checked as actual loaded Python code,
        # individually, rather than traversing SQLAlchemy/Pydantic/kernel DB
        # infrastructure as if it were the arithmetic recipe. These named
        # infrastructure boundaries remain trusted; no arbitrary DB callable
        # is accepted as a normalizer, strategy or row projector.
        for role, function in {
            "binding_identity": type(self).identity,
            "canonical": _canonical,
            "sha": _sha,
            "time": _time,
            "stored": _stored,
            "daily_asset": _daily_asset,
            "atom": _atom,
            "code": _code,
            "find_code": _find_code,
            "function": _function,
            "check_function": _check_function,
            "check_generated_init": _check_generated_init,
            "stable_recipe": stable_daily_recipe,
            "read_original": read_original_daily_input_in_session,
            "prepare_invocation": prepare_daily_computation_in_session,
            "finish_invocation": finish_daily_computation,
            "role": _role,
            "replay": _assert_replay,
            "writer_verify": verify_daily_computation_in_session,
            "writer_freeze": freeze_daily_execution_in_session,
            "historical_reader": read_daily_execution_in_session,
            "receipt_time_validation": DailyExecutionReceipt._aware,  # pyright: ignore[reportPrivateUsage]
            "receipt_digest_validation": DailyExecutionReceipt._digest,  # pyright: ignore[reportPrivateUsage]
        }.items():
            _check_function(
                role,
                function,
                source_hashes=sources,
                code_hashes=codes,
                defaults_hashes=defaults,
                visited=visited,
                compiled=compiled,
                recursive=False,
            )
        _check_generated_init(strategy.DailyBar, code_hashes=codes)
        _check_generated_init(strategy.DailyStrategyReceipt, code_hashes=codes)
        context = getcontext()
        runtime = {
            "python": sys.version,
            "implementation": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag,
            "optimize": sys.flags.optimize,
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
            "decimal": {
                "prec": context.prec,
                "rounding": context.rounding,
                "Emin": context.Emin,
                "Emax": context.Emax,
                "clamp": context.clamp,
                "capitals": context.capitals,
                "traps": [
                    [exception.__name__, enabled]
                    for exception, enabled in sorted(
                        context.traps.items(),
                        key=lambda item: item[0].__name__,
                    )
                ],
            },
            "native_dependency_scope": "python-runtime-abi;not-os-memory-attestation",
            # CPython native arithmetic/JSON and Fraction/dataclass runtime
            # implementations are an explicit trusted runtime boundary.
            # fmean/pstdev and recursively imported Python functions above are
            # source-to-loaded-code checked; this is not a whole-interpreter or
            # extension-image attestation. No claim is made about OS memory.
            "trusted_runtime_closure": [
                "builtins",
                "math",
                "hashlib/_hashlib",
                "decimal/_decimal",
                "fractions.Fraction",
                "dataclasses.make_dataclass",
                "json.scanner/_json",
                "SQLAlchemy borrowed Session/select/update;kernel persistence DTO/time",
                "Pydantic strict DTO validation;RawBodyArchive exact-byte retrieval",
            ],
            "native_bindings": {
                name: _atom(value)
                for name, value in (
                    ("math.sqrt", math.sqrt),
                    ("math.isfinite", math.isfinite),
                    ("math.fsum", math.fsum),
                    ("hashlib.sha256", hashlib.sha256),
                    ("decimal.Decimal", Decimal),
                )
            },
        }
        return DailyLoadedIdentity(
            source_hashes=sources,
            callable_code_hashes=codes,
            defaults_hashes=defaults,
            runtime=runtime,
        )


def stable_daily_recipe(identity: DailyLoadedIdentity) -> str:
    return _sha(
        _canonical(
            {
                "schema_version": "daily-stable-recipe.v1",
                "strategy_version": "daily-trend-v0.1",
                "dataset_format": "gateio-public:spot:1d",
                "parameters": {"short_window": 20, "long_window": 60},
                "loaded_identity": identity.model_dump(mode="json"),
            }
        )
    )


def read_original_daily_input_in_session(
    session: Session,
    *,
    capture_ids: Sequence[str],
    query: ResearchCapabilityQuery,
    archive: RawBodyArchive,
) -> OriginalDailyInput:
    # First slice supports exactly one actual direct Gate body, not aggregation.
    if len(capture_ids) != 1 or len(query.symbols) != 1:
        raise ValueError("daily_original_capture_set_unsupported")
    dataset = query.symbols[0]
    pair, _instrument, _units = _daily_asset(dataset)
    capture = session.get(RawCaptureRecord, capture_ids[0], populate_existing=True)
    if capture is None:
        raise ValueError("daily_original_capture_missing")
    match = _CONTEXT.fullmatch(capture.context_id)
    if match is None or match[3] != dataset:
        raise ValueError("daily_original_capture_context_mismatch")
    job = session.get(DataJobRecord, match[1], populate_existing=True)
    if job is None:
        raise ValueError("daily_original_job_missing")
    start, end = _stored(job.window_start_at), _stored(job.window_end_at)
    datasets = json.loads(job.dataset_ids_json)
    request = {
        "dataset_ids": datasets,
        "requested_by": job.requested_by,
        "window_start_at": start.isoformat(),
        "window_end_at": end.isoformat(),
    }
    if (
        not isinstance(datasets, list)
        or len(datasets) != len(set(datasets))
        or dataset not in datasets
        or _sha(_canonical(request)) != job.request_hash
    ):
        raise ValueError("daily_original_job_request_hash_mismatch")
    if query.window_start_at is None or query.window_end_at is None:
        raise ValueError("daily_original_query_window_missing")
    requested_start, requested_end = _time(query.window_start_at), _time(query.window_end_at)
    started, completed, recorded = (
        _stored(capture.started_at),
        _stored(capture.completed_at),
        _stored(capture.recorded_at),
    )
    if not (
        start < end
        and start <= requested_start < requested_end <= end
        and _stored(job.created_at) <= started <= completed <= recorded <= _time(query.cutoff_at)
        and recorded <= _time(utcnow())
        and capture.status_code == 200
        and not capture.error_code
        and capture.body_hash
        and capture.body_ref
        and 0 < capture.byte_count <= 16 * 1024 * 1024
        and capture.adapter_version == "historical-http.v1"
        and capture.media_type.split(";", 1)[0].strip().lower() == "application/json"
    ):
        raise ValueError("daily_original_capture_invalid_or_late")
    url = urlsplit(capture.url)
    parameters = parse_qsl(url.query, keep_blank_values=True)
    if (
        url.scheme != "https"
        or url.netloc != "api.gateio.ws"
        or url.path != "/api/v4/spot/candlesticks"
        or url.fragment
        or len(parameters) != 4
        or dict(parameters)
        != {
            "currency_pair": pair,
            "interval": "1d",
            "from": str(int(start.timestamp())),
            "to": str(int(end.timestamp()) - 1),
        }
    ):
        raise ValueError("daily_original_capture_url_mismatch")
    body = archive.read(capture.body_ref)
    if len(body) != capture.byte_count or _sha(body) != capture.body_hash:
        raise ValueError("daily_original_capture_body_mismatch")
    row_body = {
        column.key: (_stored(value).isoformat() if isinstance(value, datetime) else value)
        for column in RawCaptureRecord.__table__.columns
        for value in (getattr(capture, column.key),)
    }
    return OriginalDailyInput(
        job.job_id,
        job.request_hash,
        capture.capture_id,
        _sha(_canonical(row_body)),
        capture.body_hash,
        capture.body_ref,
        body,
        start,
        end,
        completed,
    )


@dataclass
class PreparedDailyComputation:
    _collector: _ComputationCollector
    _token: object
    _query_json: str
    _original: OriginalDailyInput
    _implementation: LoadedDailyImplementation
    _identity: DailyLoadedIdentity
    _rows_json: str
    _summary_json: str
    _started_at: datetime
    _used: bool = False

    @property
    def rows(self) -> list[dict[str, object]]:
        return json.loads(self._rows_json)

    @property
    def summary(self) -> dict[str, str]:
        return json.loads(self._summary_json)


@dataclass(frozen=True)
class ExecutedDailyComputation:
    receipt: DailyExecutionReceipt
    _collector: _ComputationCollector
    _token: object
    _implementation: LoadedDailyImplementation

    @property
    def receipt_json(self) -> str:
        return _canonical(self.receipt.model_dump(mode="json"))

    @property
    def strategy_json(self) -> str:
        return self.receipt.strategy_receipt_json


def prepare_daily_computation_in_session(
    session: Session,
    *,
    query: ResearchCapabilityQuery,
    capture_ids: Sequence[str],
    archive: RawBodyArchive,
    retained_rows: list[dict[str, object]],
    implementation: LoadedDailyImplementation,
) -> PreparedDailyComputation | None:
    collector = _CURRENT.get()
    if collector is None:
        # Ordinary offline calls preserve legacy behavior and mint no authority.
        return None
    if not collector.active or collector.witness is not None:
        raise ValueError("daily_computation_scope_not_current")
    outer = ResearchCapabilityQuery.model_validate_json(collector.outer_query_json)
    expected = outer.model_copy(
        update={
            "requested_observed_at": outer.observed_at,
            "observed_at": query.observed_at,
        }
    )
    if _canonical(expected.model_dump(mode="json")) != _canonical(query.model_dump(mode="json")):
        raise ValueError("daily_computation_actual_query_mismatch")
    started_at = _time(utcnow())
    identity = implementation.identity()
    original = read_original_daily_input_in_session(
        session,
        query=query,
        capture_ids=capture_ids,
        archive=archive,
    )
    normalized = implementation.normalizer(
        json.loads(original.body),
        start=original.job_start_at,
        end=original.job_end_at,
        closed_at=original.closed_at,
    )
    assert query.window_start_at is not None and query.window_end_at is not None
    selected = [
        row
        for row in normalized
        if query.window_start_at
        <= datetime.fromisoformat(str(row["timestamp"]))
        < query.window_end_at
    ]
    # Exact old partition rows must be this body projection before minting a
    # new method witness. Comparing summary/direction alone is insufficient.
    if _canonical(selected) != _canonical(retained_rows):
        raise ValueError("daily_retained_rows_differ_from_original")
    summary = implementation.summary(query.symbols[0], selected)
    if identity != implementation.identity():
        raise ValueError("daily_loaded_implementation_changed_during_prepare")
    return PreparedDailyComputation(
        collector,
        collector.token,
        _canonical(query.model_dump(mode="json")),
        original,
        implementation,
        identity,
        _canonical(selected),
        _canonical(summary),
        started_at,
    )


def finish_daily_computation(
    prepared: PreparedDailyComputation,
    *,
    candidate: EvidenceCandidate,
) -> ExecutedDailyComputation:
    collector = _CURRENT.get()
    if (
        collector is not prepared._collector  # pyright: ignore[reportPrivateUsage]
        or collector is None
        or not collector.active  # pyright: ignore[reportPrivateUsage]
        or collector.token is not prepared._token  # pyright: ignore[reportPrivateUsage]
        or prepared._used  # pyright: ignore[reportPrivateUsage]
    ):
        raise ValueError("daily_computation_invocation_not_current")
    query_json = prepared._query_json  # pyright: ignore[reportPrivateUsage]
    query = ResearchCapabilityQuery.model_validate_json(query_json)
    excerpt = json.loads(candidate.excerpt)
    selected = prepared.rows
    if (
        candidate.source_id != "gateio-public"
        or candidate.research_session_id != query.research_session_id
        or candidate.tool_call_id != query.request_id
        or candidate.round != query.round
        or candidate.requirement_id != query.requirement_id
        or excerpt["dataset_id"] != query.symbols[0]
        or excerpt["derived_from_hourly"] is not False
        or excerpt["summary"]
        != {key: prepared.summary[key] for key in sorted(set(query.fields) or prepared.summary)}
    ):
        raise ValueError("daily_computation_candidate_projection_mismatch")
    if prepared._identity != prepared._implementation.identity():  # pyright: ignore[reportPrivateUsage]
        raise ValueError("daily_loaded_implementation_changed_before_compute")
    rows = prepared._implementation.row_projector(selected)  # pyright: ignore[reportPrivateUsage]
    strategy_receipt = strategy.compute_daily_strategy(
        rows,
        as_of=str(selected[-1]["timestamp"])[:10],
        evidence_refs=(candidate.evidence_id,),
        short_window=20,
        long_window=60,
    )
    strategy_json = strategy_receipt.as_json()
    completed_at = _time(utcnow())
    if prepared._identity != prepared._implementation.identity():  # pyright: ignore[reportPrivateUsage]
        raise ValueError("daily_loaded_implementation_changed_during_compute")
    assert query.window_start_at is not None and query.window_end_at is not None
    receipt = DailyExecutionReceipt(
        run_id=collector.run_id,
        request_id=query.request_id,
        research_session_id=query.research_session_id,
        generation=query.round,
        reservation_query_hash=collector.reservation_query_hash,
        adapter_query_json=prepared._query_json,  # pyright: ignore[reportPrivateUsage]
        adapter_query_hash=_sha(prepared._query_json),  # pyright: ignore[reportPrivateUsage]
        job_id=prepared._original.job_id,  # pyright: ignore[reportPrivateUsage]
        job_request_hash=prepared._original.job_request_hash,  # pyright: ignore[reportPrivateUsage]
        capture_id=prepared._original.capture_id,  # pyright: ignore[reportPrivateUsage]
        capture_row_hash=prepared._original.capture_row_hash,  # pyright: ignore[reportPrivateUsage]
        body_hash=prepared._original.body_hash,  # pyright: ignore[reportPrivateUsage]
        body_ref=prepared._original.body_ref,  # pyright: ignore[reportPrivateUsage]
        query_start_at=query.window_start_at,
        query_end_at=query.window_end_at,
        closed_at=prepared._original.closed_at,  # pyright: ignore[reportPrivateUsage]
        normalized_rows_hash=_sha(prepared._rows_json),  # pyright: ignore[reportPrivateUsage]
        strategy_input_hash=strategy_receipt.data_hash,
        summary_json=prepared._summary_json,  # pyright: ignore[reportPrivateUsage]
        strategy_receipt_json=strategy_json,  # pyright: ignore[reportPrivateUsage]
        computed_started_at=prepared._started_at,  # pyright: ignore[reportPrivateUsage]
        computed_completed_at=completed_at,  # pyright: ignore[reportPrivateUsage]
        loaded_identity=prepared._identity,  # pyright: ignore[reportPrivateUsage]
        stable_recipe_hash=stable_daily_recipe(prepared._identity),  # pyright: ignore[reportPrivateUsage]
    )
    witness = ExecutedDailyComputation(
        receipt,
        collector,
        collector.token,
        prepared._implementation,  # pyright: ignore[reportPrivateUsage]
    )
    prepared._used = True  # pyright: ignore[reportPrivateUsage]
    collector.witness = witness
    return witness


def _role(request_id: str) -> str:
    return ROLE_PREFIX + _sha(request_id)[:40]


def _assert_replay(
    session: Session,
    *,
    receipt: DailyExecutionReceipt,
    archive: RawBodyArchive,
    implementation: LoadedDailyImplementation,
) -> None:
    query = ResearchCapabilityQuery.model_validate_json(receipt.adapter_query_json)
    if (
        receipt.adapter_query_json != _canonical(query.model_dump(mode="json"))
        or query.capability_id != "data.historical"
        or (query.request_id, query.research_session_id, query.round)
        != (receipt.request_id, receipt.research_session_id, receipt.generation)
        or query.window_start_at != receipt.query_start_at
        or query.window_end_at != receipt.query_end_at
        or receipt.computed_started_at > receipt.computed_completed_at
    ):
        raise ValueError("daily_execution_query_identity_mismatch")
    original = read_original_daily_input_in_session(
        session,
        capture_ids=[receipt.capture_id],
        query=query,
        archive=archive,
    )
    if (
        receipt.adapter_query_hash != _sha(receipt.adapter_query_json)
        or (
            receipt.job_id,
            receipt.job_request_hash,
            receipt.capture_row_hash,
            receipt.body_hash,
            receipt.body_ref,
            receipt.closed_at,
        )
        != (
            original.job_id,
            original.job_request_hash,
            original.capture_row_hash,
            original.body_hash,
            original.body_ref,
            original.closed_at,
        )
        or receipt.loaded_identity != implementation.identity()
        or receipt.stable_recipe_hash != stable_daily_recipe(receipt.loaded_identity)
        or original.closed_at > receipt.computed_started_at
    ):
        raise ValueError("daily_execution_original_or_loaded_identity_mismatch")
    normalized = implementation.normalizer(
        json.loads(original.body),
        start=original.job_start_at,
        end=original.job_end_at,
        closed_at=original.closed_at,
    )
    selected = [
        row
        for row in normalized
        if receipt.query_start_at
        <= datetime.fromisoformat(str(row["timestamp"]))
        < receipt.query_end_at
    ]
    saved = json.loads(receipt.strategy_receipt_json)
    computed = strategy.compute_daily_strategy(
        implementation.row_projector(selected),
        as_of=str(selected[-1]["timestamp"])[:10],
        evidence_refs=saved["evidence_refs"],
        short_window=20,
        long_window=60,
    )
    if (
        _sha(_canonical(selected)) != receipt.normalized_rows_hash
        or computed.data_hash != receipt.strategy_input_hash
        or _canonical(implementation.summary(query.symbols[0], selected)) != receipt.summary_json
        or computed.as_json() != receipt.strategy_receipt_json
        or receipt.loaded_identity != implementation.identity()
    ):
        raise ValueError("daily_execution_full_replay_mismatch")


def verify_daily_computation_in_session(
    session: Session,
    *,
    collector: _ComputationCollector,
    raw_adapter_return: CapabilityAdapterReturn,
    result: ResearchCapabilityResult,
    archive: RawBodyArchive,
) -> ExecutedDailyComputation | None:
    receipt_facts = [fact for fact in result.facts or [] if ATTRIBUTE in fact.attributes]
    witness = collector.witness
    if witness is None:
        if receipt_facts:
            raise ValueError("daily_execution_json_without_actual_witness")
        return None
    receipt = witness.receipt
    if (
        collector.active
        or _CURRENT.get() is collector
        or witness._collector is not collector  # pyright: ignore[reportPrivateUsage]
        or witness._token is not collector.token  # pyright: ignore[reportPrivateUsage]
        or receipt.run_id != collector.run_id
        or receipt.reservation_query_hash != collector.reservation_query_hash
        or receipt.adapter_query_json != raw_adapter_return.effective_query_json
        or receipt.computed_started_at < _time(raw_adapter_return.server_received_at)
        or receipt.computed_started_at > receipt.computed_completed_at
        or receipt.computed_completed_at > _time(utcnow())
        or not receipt_facts
        or any(fact.attributes[ATTRIBUTE] != witness.receipt_json for fact in receipt_facts)
        or any(
            fact.attributes.get("daily_strategy_receipt_json") != receipt.strategy_receipt_json
            for fact in receipt_facts
        )
    ):
        raise ValueError("daily_execution_actual_return_mismatch")
    original = raw_adapter_return.result()
    if receipt.computed_completed_at > _time(original.completed_at):
        raise ValueError("daily_execution_completed_after_adapter_return")
    if [fact.model_dump(mode="json")["attributes"] for fact in original.facts or []] != [
        fact.model_dump(mode="json")["attributes"] for fact in result.facts or []
    ]:
        raise ValueError("daily_execution_attributes_changed_after_return")
    summary = json.loads(receipt.summary_json)
    evidence_refs = json.loads(receipt.strategy_receipt_json)["evidence_refs"]
    if len(receipt_facts) != len(result.facts or []) or len(evidence_refs) != 1:
        raise ValueError("daily_execution_return_fact_set_mismatch")
    query = ResearchCapabilityQuery.model_validate_json(receipt.adapter_query_json)
    if len(query.symbols) != 1:
        raise ValueError("daily_execution_dataset_unsupported")
    dataset = query.symbols[0]
    _pair, instrument, units = _daily_asset(dataset)
    implementation = witness._implementation  # pyright: ignore[reportPrivateUsage]
    for fact in receipt_facts:
        if (
            fact.evidence_id != evidence_refs[0]
            or fact.requirement_id != query.requirement_id
            or fact.field not in summary
            or fact.value != summary[fact.field]
            or fact.unit != implementation.units(dataset, fact.field, units)
            or fact.instrument != instrument
            or fact.venue != "gateio"
            or fact.source_id != "gateio-public"
            or fact.metric_family != "crypto.spot.history"
            or fact.window_start_at != receipt.query_start_at
            or fact.window_end_at != receipt.query_end_at
            or fact.attributes.get("dataset_id") != dataset
            or fact.attributes.get("derived_from_hourly") is not False
            or fact.attributes.get("calculation_version") != "historical-window-summary.v1"
        ):
            raise ValueError("daily_execution_actual_fact_projection_mismatch")
    _assert_replay(
        session, receipt=receipt, archive=archive, implementation=implementation
    )  # pyright: ignore[reportPrivateUsage]
    return witness


def freeze_daily_execution_in_session(
    session: Session,
    *,
    collector: _ComputationCollector,
    witness: ExecutedDailyComputation,
    raw_adapter_return: CapabilityAdapterReturn,
    result: ResearchCapabilityResult,
    archive: RawBodyArchive,
    raw_audit_anchor: str,
    recorded_at: datetime,
    admission_authorized: bool,
) -> str | None:
    """Call after semantic lineage flush, still in the same Gateway savepoint.

    false preserves cancellation's existing late facts/audit/cost and adds no
    authoritative method role. It does not change the returned raw bytes.
    """
    if (
        verify_daily_computation_in_session(
            session,
            collector=collector,
            raw_adapter_return=raw_adapter_return,
            result=result,
            archive=archive,
        )
        is not witness
    ):
        raise ValueError("daily_execution_witness_changed")
    now = _time(recorded_at)
    receipt = witness.receipt
    # Actual computation in a cancelled call remains visible in Fact attributes;
    # it cannot acquire a new method-authority role after the cancellation.
    if not admission_authorized:
        return None
    connection = session.connection()
    native = connection.connection.driver_connection
    if connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection):
        raise ValueError("daily_execution_writer_sqlite_required")
    if not native.in_transaction:
        raise ValueError("daily_execution_existing_writer_required")
    run = session.get(RunRecord, receipt.run_id, populate_existing=True)
    reservation = session.get(
        ResearchToolCallReservationRecord,
        (receipt.run_id, receipt.request_id),
        populate_existing=True,
    )
    if (
        run is None
        or run.status != "running"
        or run.artifact_id is not None
        or not collector.expected_lease_owner
        or run.lease_owner != collector.expected_lease_owner
        or _stored(run.lease_expires_at) <= _time(utcnow())
        or reservation is None
        or reservation.query_hash != receipt.reservation_query_hash
        or reservation.research_session_id != receipt.research_session_id
        or reservation.generation != receipt.generation
        or reservation.capability_id != "data.historical"
        or reservation.status != "running"
        or receipt.computed_completed_at > now
    ):
        raise PermissionError("daily_execution_writer_permission_lost")
    facts = session.scalars(
        select(ResearchFactRecord)
        .where(
            ResearchFactRecord.run_id == receipt.run_id,
            ResearchFactRecord.evidence_id.in_(
                json.loads(receipt.strategy_receipt_json)["evidence_refs"],
            ),
        )
        .execution_options(populate_existing=True)
    ).all()
    expected = {fact.fact_id: fact for fact in result.facts or []}
    if (
        not facts
        or set(expected) != {fact.fact_id for fact in facts}
        or any(
            fact.quality != "accepted"
            or json.loads(fact.attributes_json).get(ATTRIBUTE) != witness.receipt_json
            or _fact_model(fact).model_dump(mode="json")
            != expected[fact.fact_id].model_dump(mode="json")
            for fact in facts
        )
    ):
        raise ValueError("daily_execution_saved_fact_binding_mismatch")
    for evidence_id in json.loads(receipt.strategy_receipt_json)["evidence_refs"]:
        evidence = session.get(ResearchEvidenceRecord, evidence_id, populate_existing=True)
        if evidence is None or evidence.run_id != receipt.run_id or evidence.quality != "accepted":
            raise ValueError("daily_execution_saved_evidence_not_accepted")
    body = {
        "schema_version": "daily-execution-admission.v1",
        "receipt": receipt.model_dump(mode="json"),
        "adapter_return_hash": raw_adapter_return.result_hash,
        "raw_audit_anchor": raw_audit_anchor,
        "fact_ids": sorted(fact.fact_id for fact in facts),
        "recorded_at": now.isoformat(),
    }
    payload = _canonical(body)
    digest = _sha(payload)
    role = _role(receipt.request_id)
    existing = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == receipt.run_id,
            RunRoleOutputRecord.role == role,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if (
            existing.output_id != "daily_execution_" + digest
            or existing.schema_version != body["schema_version"]
            or existing.payload_hash != digest
            or existing.payload_json != payload
        ):
            raise ValueError("daily_execution_original_conflict")
    else:
        session.add(
            RunRoleOutputRecord(
                output_id="daily_execution_" + digest,
                run_id=receipt.run_id,
                role=role,
                schema_version=body["schema_version"],
                payload_json=payload,
                payload_hash=digest,
                created_at=now,
            )
        )
    session.flush()
    fenced = session.execute(
        update(RunRecord)
        .where(
            RunRecord.run_id == receipt.run_id,
            RunRecord.status == "running",
            RunRecord.artifact_id.is_(None),
            RunRecord.lease_owner == collector.expected_lease_owner,
            RunRecord.lease_expires_at > _time(utcnow()),
        )
        .values(updated_at=RunRecord.updated_at)
        .execution_options(synchronize_session=False)
    )
    if getattr(fenced, "rowcount", None) != 1:
        raise PermissionError("daily_execution_final_writer_permission_lost")
    return "daily_execution_" + digest


@dataclass(frozen=True)
class FrozenDailyExecution:
    output_id: str
    role: str
    schema_version: str
    payload_hash: str
    receipt: DailyExecutionReceipt
    stable_recipe_hash: str


def read_daily_execution_in_session(
    session: Session,
    *,
    run_id: str,
    evidence_id: str,
    receipt_json: str,
    archive: RawBodyArchive,
    implementation: LoadedDailyImplementation,
    publication_at: datetime,
    assessed_at: datetime,
) -> FrozenDailyExecution | None:
    """Method authority only; caller must separately authenticate parent/source.

    Old/late missing admission role returns None; a present but bad role fails.
    This does not open a Session, write, backfill, or accept any claimed pass.
    """
    receipt = DailyExecutionReceipt.model_validate_json(receipt_json)
    if (
        receipt_json != _canonical(receipt.model_dump(mode="json"))
        or receipt.run_id != run_id
        or json.loads(receipt.strategy_receipt_json)["evidence_refs"] != [evidence_id]
    ):
        raise ValueError("daily_execution_receipt_binding_mismatch")
    role = _role(receipt.request_id)
    row = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == role,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    body = json.loads(row.payload_json)
    digest = _sha(row.payload_json)
    if (
        set(body)
        != {
            "schema_version",
            "receipt",
            "adapter_return_hash",
            "raw_audit_anchor",
            "fact_ids",
            "recorded_at",
        }
        or row.payload_json != _canonical(body)
        or row.schema_version != "daily-execution-admission.v1"
        or body["schema_version"] != row.schema_version
        or row.output_id != "daily_execution_" + digest
        or row.payload_hash != digest
        or body["receipt"] != receipt.model_dump(mode="json")
        or body["recorded_at"] != _stored(row.created_at).isoformat()
        or _stored(row.created_at) > _time(publication_at)
        or _time(publication_at) > _time(assessed_at)
        or receipt.computed_started_at > receipt.computed_completed_at
        or receipt.computed_completed_at > _stored(row.created_at)
    ):
        raise ValueError("daily_execution_admission_original_invalid")
    raw_role = "research_adapter_return:" + _sha(receipt.request_id)[:40]
    raw = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == raw_role,
        )
        .execution_options(populate_existing=True)
    )
    if raw is None:
        raise ValueError("daily_execution_adapter_original_missing")
    raw_digest = _sha(raw.payload_json)
    original = json.loads(raw.payload_json)
    if (
        not isinstance(original, dict)
        or set(original)
        != {
            "schema_version",
            "run_id",
            "request_id",
            "research_session_id",
            "generation",
            "capability_id",
            "reservation_query_hash",
            "effective_adapter_query_json",
            "effective_adapter_query_hash",
            "server_received_at",
            "adapter_result_json",
            "adapter_result_hash",
        }
        or raw.payload_json != _canonical(original)
        or raw.output_id != "adapter_return_" + raw_digest
        or raw.payload_hash != raw_digest
        or raw.schema_version != "research-adapter-return-observation.v1"
        or original["schema_version"] != raw.schema_version
        or original["run_id"] != run_id
        or original["request_id"] != receipt.request_id
        or original["research_session_id"] != receipt.research_session_id
        or original["generation"] != receipt.generation
        or original["capability_id"] != "data.historical"
        or original["reservation_query_hash"] != receipt.reservation_query_hash
        or original["effective_adapter_query_json"] != receipt.adapter_query_json
        or original["effective_adapter_query_hash"] != receipt.adapter_query_hash
        or original["adapter_result_hash"] != body["adapter_return_hash"]
        or _sha(original["adapter_result_json"]) != body["adapter_return_hash"]
        or body["raw_audit_anchor"] != f" [adapter-return.v1:{raw.output_id}:{raw.payload_hash}]"
        or _stored(raw.created_at) > _stored(row.created_at)
    ):
        raise ValueError("daily_execution_adapter_original_mismatch")
    result = ResearchCapabilityResult.model_validate_json(original["adapter_result_json"])
    if (
        receipt.computed_started_at < _time(datetime.fromisoformat(original["server_received_at"]))
        or receipt.computed_completed_at > _time(result.completed_at)
        or not result.facts
        or sorted(fact.fact_id for fact in result.facts) != body["fact_ids"]
        or any(fact.attributes.get(ATTRIBUTE) != receipt_json for fact in result.facts)
    ):
        raise ValueError("daily_execution_original_result_mismatch")
    for fact in result.facts:
        persisted = session.get(ResearchFactRecord, fact.fact_id, populate_existing=True)
        if (
            persisted is None
            or persisted.run_id != run_id
            or persisted.evidence_id != evidence_id
            or persisted.quality != "accepted"
            or persisted.payload_hash != fact.payload_hash
            or json.loads(persisted.attributes_json) != fact.attributes
        ):
            raise ValueError("daily_execution_saved_original_fact_mismatch")
    _assert_replay(session, receipt=receipt, archive=archive, implementation=implementation)
    return FrozenDailyExecution(
        row.output_id,
        row.role,
        row.schema_version,
        row.payload_hash,
        receipt,
        receipt.stable_recipe_hash,
    )
