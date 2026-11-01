"""Strict new binding shapes from allowlisted canonical sources only.

Shape validation is not proof of persisted provenance or checker execution.
Existing contract bases and v1 readers remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from asyncio import current_task
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import get_ident
from types import FunctionType, MethodType
from typing import Any, cast
from urllib.parse import urldefrag, urljoin

import jsonschema._format as canonical_formats
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, model_validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012

from . import context_selection_semantics as selection_semantics
from .policy_contract_base import policy_format_checker

SCHEMA_FILES = (
    "research_binding.schema.yaml",
    "agentic_research.schema.yaml",
    "research_policy.schema.yaml",
    "research_fact.schema.yaml",
    "research_library.schema.yaml",
)
MAX_SCHEMA_BYTES = 262_144
MAX_SHAPE_MEMO_ENTRIES = 256
MAX_SHAPE_MEMO_VALUE_BYTES = 32_768
MAX_SHAPE_MEMO_TOTAL_BYTES = 1_048_576
_DRAFT_VALIDATE = Draft202012Validator.validate
_DRAFT_KEYWORDS = dict(Draft202012Validator.VALIDATORS)
_DRAFT_TYPE_CHECKER = Draft202012Validator.TYPE_CHECKER
_DRAFT_FUNCTION_METADATA = tuple(
    (
        function,
        function.__code__,
        function.__globals__,
        function.__defaults__,
        function.__kwdefaults__,
        function.__closure__,
    )
    for function in (_DRAFT_VALIDATE, *_DRAFT_KEYWORDS.values())
    if type(function) is FunctionType
)
_DEFAULT_RETRIEVE = (
    Draft202012Validator({})._registry._retrieve  # pyright: ignore[reportPrivateUsage]
)
_RESOLVER_TYPE = type(
    Draft202012Validator({})._resolver  # pyright: ignore[reportPrivateUsage]
)
_REFERENCE_OPERATIONS = tuple(
    (owner, name, getattr(owner, name), getattr(getattr(owner, name), "__code__", None))
    for owner, name in (
        (_RESOLVER_TYPE, "lookup"),
        (Registry, "get_or_retrieve"),
        (Resource, "pointer"),
        (DRAFT202012, "id_of"),
        (DRAFT202012, "subresources_of"),
        (DRAFT202012, "maybe_in_subresource"),
        (DRAFT202012, "anchors_in"),
    )
)
_REFERENCE_FUNCTION_METADATA = tuple(
    (
        function,
        function.__code__,
        function.__globals__,
        function.__defaults__,
        function.__kwdefaults__,
        function.__closure__,
    )
    for _owner, _name, operation, _code in _REFERENCE_OPERATIONS
    for function in (operation.__func__ if type(operation) is MethodType else operation,)
    if type(function) is FunctionType
)
_POLICY_RULE_REF_PREFIX = "https://decision-hub.local/contracts/research_policy.schema.json#/$defs/"
_POLICY_RULE_DEFINITIONS = frozenset(
    {
        "research_pit_rule",
        "research_frozen_hard_coverage_rule",
        "research_citation_membership_rule",
        "research_citation_authority_rule",
        "research_citation_precision_review_rule",
        "research_gap_to_tool_continuation_rule",
        "research_bounded_stop_consistency_rule",
        "research_causal_chain_structure_rule",
        "research_causal_quality_review_rule",
        "research_counter_thesis_quality_review_rule",
        "research_horizon_contract_rule",
        "research_probability_provenance_rule",
        "research_kernel_cap_rule",
        "research_execution_permission_rule",
    }
)


def _context_selection_aware_gregorian(value: Any) -> bool:
    # Identical to the V3 declaration's original Gregorian/RFC3339 checker.
    return not isinstance(value, str) or selection_semantics.timestamp_shape_valid(value)


def context_selection_shape_format_checker() -> FormatChecker:
    """The canonical V3 pure checker, without an extensible registration API."""
    checker = FormatChecker()
    checker.checks("date-time")(_context_selection_aware_gregorian)
    return checker


_CONTEXT_SELECTION_FORMATTER = _context_selection_aware_gregorian
_TIMESTAMP_MODULE = selection_semantics
_TIMESTAMP_FUNCTIONS = tuple(
    (
        function,
        function.__code__,
        function.__globals__,
        function.__defaults__,
        function.__kwdefaults__,
    )
    for function in (
        selection_semantics.timestamp_shape_valid,
        selection_semantics._parts,  # pyright: ignore[reportPrivateUsage]
    )
)
_TIMESTAMP_BINDINGS = tuple(
    (name, getattr(selection_semantics, name))
    for name in (
        "timestamp_shape_valid",
        "_parts",
        "_TIMESTAMP",
        "date",
        "_UTC_LIMIT_US",
        "ContextSelectionSemanticError",
    )
)
_PURE_FORMAT_FUNCTIONS = tuple(
    (
        function,
        raises,
        getattr(function, "__code__", None),
        getattr(function, "__globals__", None),
        getattr(function, "__defaults__", None),
        getattr(function, "__kwdefaults__", None),
    )
    for function, raises in policy_format_checker().checkers.values()
    if type(function) is FunctionType
    and (
        getattr(canonical_formats, function.__name__, None) is function
        or function.__module__ == __name__.rsplit(".", 1)[0] + ".policy_contract_base"
        and function.__qualname__ == "policy_format_checker.<locals>.aware_datetime"
    )
) + (
    (
        _context_selection_aware_gregorian,
        (),
        _context_selection_aware_gregorian.__code__,
        _context_selection_aware_gregorian.__globals__,
        _context_selection_aware_gregorian.__defaults__,
        _context_selection_aware_gregorian.__kwdefaults__,
    ),
)
_POLICY_FORMAT_FUNCTIONS = tuple(
    known
    for known, _raises, _code, _namespace, _defaults, _kwdefaults in _PURE_FORMAT_FUNCTIONS
    if known.__qualname__ == "policy_format_checker.<locals>.aware_datetime"
)
_POLICY_FORMAT_BINDINGS = tuple(
    (
        typed_namespace,
        typed_namespace["re"],
        typed_namespace["datetime"],
        typed_namespace["re"].fullmatch,
        typed_namespace["re"].fullmatch.__code__,
        typed_namespace["re"].fullmatch.__globals__,
        typed_namespace["re"].fullmatch.__defaults__,
        typed_namespace["re"].fullmatch.__kwdefaults__,
        typed_namespace["re"].fullmatch.__closure__,
    )
    for known, _raises, _code, namespace, _defaults, _kwdefaults in _PURE_FORMAT_FUNCTIONS
    if any(known is original for original in _POLICY_FORMAT_FUNCTIONS)
    for typed_namespace in (cast(dict[str, Any], namespace),)
)


@dataclass
class ShapeValidationStats:
    """Diagnostic counters only; no cached value or provenance is exposed."""

    hit_count: int = 0
    miss_count: int = 0
    bypass_count: int = 0
    fast_reject_count: int = 0
    schema_snapshot_hit_count: int = 0


@dataclass(frozen=True, slots=True)
class _SchemaReference:
    parent: int
    target: int
    reference: str


@dataclass(frozen=True, slots=True)
class _SchemaSnapshot:
    # Keeping the actual validator alive also prevents an id-reuse collision.
    validator: Draft202012Validator
    signature: bytes
    header: tuple[Any, ...]
    bindings: tuple[tuple[str, Any], ...]
    documents: tuple[Any, ...]
    bases: tuple[str, ...]
    references: tuple[_SchemaReference, ...]
    retained_bytes: int


@dataclass
class _ShapeMemo:
    owner_thread: int
    owner_task: object | None
    stats: ShapeValidationStats = field(default_factory=ShapeValidationStats)
    successes: set[tuple[type[BaseModel], int, bytes, bytes]] = field(default_factory=set)
    schemas: dict[int, _SchemaSnapshot] = field(default_factory=dict)
    retained_bytes: int = 0
    active: bool = True


_SHAPE_MEMO: ContextVar[_ShapeMemo | None] = ContextVar("binding_shape_success_memo", default=None)


def _task_identity() -> object | None:
    try:
        return current_task()
    except RuntimeError:
        return None


def _owned(memo: _ShapeMemo) -> bool:
    return (
        memo.active and memo.owner_thread == get_ident() and (memo.owner_task is _task_identity())
    )


@contextmanager
def shape_validation_scope() -> Generator[ShapeValidationStats]:
    """Explicit single-call reuse of pure successful JSON shape checks only.

    Fresh projection and normal Pydantic validation still run on every call.
    Copying an async context or running a reader thread never grants that child
    access to its parent's successes. Only the outer owner clears a nested scope.
    """
    existing = _SHAPE_MEMO.get()
    if existing is not None and _owned(existing):
        yield existing.stats
        return
    memo = _ShapeMemo(get_ident(), _task_identity())
    token = _SHAPE_MEMO.set(memo)
    try:
        yield memo.stats
    finally:
        memo.active = False
        memo.successes.clear()
        memo.schemas.clear()
        memo.retained_bytes = 0
        _SHAPE_MEMO.reset(token)


def _native_json(value: object, *, depth: int = 0) -> bool:
    """No tuple/list, bool/int, subclass, or custom-equality key collisions."""
    if depth > 64:
        return False
    kind = type(value)
    if kind is str or kind is int or kind is bool or kind is type(None):
        return True
    if kind is float:
        return math.isfinite(cast(float, value))
    if kind is list:
        return all(_native_json(item, depth=depth + 1) for item in cast(list[object], value))
    if kind is dict:
        return all(
            type(key) is str and _native_json(item, depth=depth + 1)
            for key, item in cast(dict[object, object], value).items()
        )
    return False


def _native_bytes(value: object, limit: int | None = None) -> bytes | None:
    if not _native_json(value):
        return None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None
    return encoded if limit is None or len(encoded) <= limit else None


def _pure_format(function: Any, raises: Any) -> bool:
    for known, known_raises, code, namespace, defaults, kwdefaults in _PURE_FORMAT_FUNCTIONS:
        same_raises = raises is known_raises or (
            type(raises) is tuple
            and type(known_raises) is tuple
            and len(raises) == len(cast(tuple[type[Exception], ...], known_raises))
            and all(
                left is right
                for left, right in zip(
                    raises,
                    cast(tuple[type[Exception], ...], known_raises),
                    strict=True,
                )
            )
        )
        if not same_raises:
            continue
        if code is None and function is known:
            return True
        if (
            type(function) is FunctionType
            and function.__code__ is code
            and function.__globals__ is namespace
            and function.__defaults__ is defaults
            and function.__kwdefaults__ is kwdefaults
            and not function.__closure__
        ):
            if any(known is original for original in _POLICY_FORMAT_FUNCTIONS) and any(
                namespace.get("re") is not module
                or namespace.get("datetime") is not original_datetime
                or module.fullmatch is not fullmatch
                or fullmatch.__code__ is not fullmatch_code
                or fullmatch.__globals__ is not fullmatch_globals
                or fullmatch.__defaults__ is not fullmatch_defaults
                or fullmatch.__kwdefaults__ is not fullmatch_kwdefaults
                or fullmatch.__closure__ is not fullmatch_closure
                for (
                    namespace,
                    module,
                    original_datetime,
                    fullmatch,
                    fullmatch_code,
                    fullmatch_globals,
                    fullmatch_defaults,
                    fullmatch_kwdefaults,
                    fullmatch_closure,
                ) in _POLICY_FORMAT_BINDINGS
            ):
                return False
            if known is _CONTEXT_SELECTION_FORMATTER and (
                selection_semantics is not _TIMESTAMP_MODULE
                or any(
                    getattr(selection_semantics, name, None) is not original
                    for name, original in _TIMESTAMP_BINDINGS
                )
                or any(
                    original.__code__ is not original_code
                    or original.__globals__ is not original_namespace
                    or original.__defaults__ is not original_defaults
                    or original.__kwdefaults__ is not original_kwdefaults
                    or original.__closure__ is not None
                    for (
                        original,
                        original_code,
                        original_namespace,
                        original_defaults,
                        original_kwdefaults,
                    ) in _TIMESTAMP_FUNCTIONS
                )
            ):
                return False
            return True
    return False


def _rule_discriminator_mismatch(validator: Draft202012Validator, projected: Any) -> bool:
    """Reject only a proven wrong tag in an original canonical rule branch.

    This is a fresh necessary-constraint check, not cached failed validation.
    All matching tags and unknown validation extensions keep the complete path.
    """
    try:
        return _rule_discriminator_mismatch_checked(validator, projected)
    except Exception:
        # An optimization must not replace the validator's own error behavior.
        return False


def _rule_discriminator_mismatch_checked(
    validator: Draft202012Validator,
    projected: Any,
) -> bool:
    if (
        type(projected) is not dict
        or any(type(key) is not str for key in projected)
        or type(projected.get("rule_id")) is not str
    ):
        return False
    if (
        type(validator) is not Draft202012Validator
        or Draft202012Validator.validate is not _DRAFT_VALIDATE
        or validator.TYPE_CHECKER is not _DRAFT_TYPE_CHECKER
        or type(validator.VALIDATORS) is not dict
        or len(validator.VALIDATORS) != len(_DRAFT_KEYWORDS)
        or any(
            type(name) is not str or function is not _DRAFT_KEYWORDS.get(name)
            for name, function in validator.VALIDATORS.items()
        )
    ):
        return False
    checker = validator.format_checker
    if checker is not None and (
        type(checker) is not FormatChecker
        or type(checker.checkers) is not dict
        or any(type(name) is not str for name in checker.checkers)
        or any(not _pure_format(function, raises) for function, raises in checker.checkers.values())
    ):
        return False
    schema = validator.schema
    if (
        type(schema) is not dict
        or any(type(key) is not str for key in schema)
        or tuple(schema) != ("$ref",)
    ):
        return False
    reference = schema["$ref"]
    if type(reference) is not str or not reference.startswith(_POLICY_RULE_REF_PREFIX):
        return False
    if reference[len(_POLICY_RULE_REF_PREFIX) :] not in _POLICY_RULE_DEFINITIONS:
        return False
    bound = validator._validators  # pyright: ignore[reportPrivateUsage]
    if (
        type(bound) is not list
        or len(bound) != 1
        or type(bound[0]) is not tuple
        or len(bound[0]) != 3
    ):
        return False
    function, keyword, original_ref = bound[0]
    if (
        function is not _DRAFT_KEYWORDS["$ref"]
        or type(keyword) is not str
        or keyword != "$ref"
        or type(original_ref) is not str
        or original_ref != reference
    ):
        return False
    registry = validator._registry  # pyright: ignore[reportPrivateUsage]
    resolver = validator._resolver  # pyright: ignore[reportPrivateUsage]
    legacy = validator._ref_resolver  # pyright: ignore[reportPrivateUsage]
    if type(registry) is not Registry or type(resolver) is not _RESOLVER_TYPE or legacy is not None:
        return False
    retrieve = registry._retrieve  # pyright: ignore[reportPrivateUsage]
    effective = resolver._registry  # pyright: ignore[reportPrivateUsage]
    if type(effective) is not Registry:
        return False
    effective_retrieve = effective._retrieve  # pyright: ignore[reportPrivateUsage]
    if (
        retrieve is not _deny_retrieve
        and retrieve is not _DEFAULT_RETRIEVE
        or effective_retrieve is not _deny_retrieve
        and effective_retrieve is not _DEFAULT_RETRIEVE
    ):
        return False
    base = resolver._base_uri  # pyright: ignore[reportPrivateUsage]
    if type(base) is not str or urldefrag(urljoin(base, reference))[0] not in effective:
        return False  # Never retrieve an unregistered resource for a tag check.
    definition = resolver.lookup(reference).contents
    if (
        type(definition) is not dict
        or any(type(key) is not str for key in definition)
        or set(definition) - {"type", "additionalProperties", "properties", "required", "title"}
        or type(definition.get("type")) is not str
        or definition["type"] != "object"
    ):
        return False
    required, properties = definition.get("required"), definition.get("properties")
    if (
        type(required) is not list
        or any(type(name) is not str for name in required)
        or "rule_id" not in required
        or type(properties) is not dict
        or any(type(key) is not str for key in properties)
    ):
        return False
    tag = properties.get("rule_id")
    if (
        type(tag) is not dict
        or any(type(key) is not str for key in tag)
        or set(tag) != {"type", "const"}
        or type(tag.get("type")) is not str
        or tag["type"] != "string"
        or type(tag.get("const")) is not str
    ):
        return False
    return projected["rule_id"] != tag["const"]


def _shape_schema_signature(validator: Draft202012Validator) -> bytes | None:
    try:
        return _shape_schema_signature_checked(validator)
    except Exception:
        # Bookkeeping must never introduce a new acceptance or error path.
        # The unchanged validator below remains responsible for every failure.
        return None


def _shape_schema_signature_checked(validator: Draft202012Validator) -> bytes | None:
    """Fingerprint effective reachable definitions, never fetch a new resource.

    Registry containers are immutable but their Resource.contents are not.
    Every candidate hit therefore resolves and encodes the current transitive
    definitions again. No full document or validated DTO is kept in the memo.
    Unknown/stateful validation extensions deliberately retain the original path.
    """
    if not _reference_operations_unchanged() or (
        type(validator) is not Draft202012Validator
        or Draft202012Validator.validate is not _DRAFT_VALIDATE
        or validator.TYPE_CHECKER is not _DRAFT_TYPE_CHECKER
        or type(validator.VALIDATORS) is not dict
        or len(validator.VALIDATORS) != len(_DRAFT_KEYWORDS)
        or any(
            type(name) is not str or function is not _DRAFT_KEYWORDS.get(name)
            for name, function in validator.VALIDATORS.items()
        )
    ):
        return None
    registry = validator._registry  # pyright: ignore[reportPrivateUsage]
    resolver = validator._resolver  # pyright: ignore[reportPrivateUsage]
    retrieve = registry._retrieve  # pyright: ignore[reportPrivateUsage]
    legacy_resolver = validator._ref_resolver  # pyright: ignore[reportPrivateUsage]
    if (
        resolver is None
        or legacy_resolver is not None
        or retrieve is not _deny_retrieve
        and retrieve is not _DEFAULT_RETRIEVE
    ):
        return None
    signature = hashlib.sha256()
    signature.update(str((id(registry), id(resolver), id(validator.TYPE_CHECKER))).encode())
    checker = validator.format_checker
    if checker is not None:
        if type(checker) is not FormatChecker:
            return None
        signature.update(str(id(checker)).encode())
        for name, (function, raises) in sorted(checker.checkers.items()):
            if not _pure_format(function, raises):
                return None
            signature.update(
                repr((name, id(function), id(getattr(function, "__code__", None)), raises)).encode()
            )
    # jsonschema binds keyword functions and child values during construction.
    # Include their actual current bindings, not only the public root schema.
    for function, keyword, bound in validator._validators:  # pyright: ignore[reportPrivateUsage]
        if function is not _DRAFT_KEYWORDS.get(keyword):
            return None
        body = _native_bytes(bound)
        if body is None:
            return None
        signature.update(keyword.encode())
        signature.update(body)
    pending: list[tuple[Any, Any]] = [(validator.schema, resolver)]
    seen: set[tuple[str, str]] = set()
    while pending:
        document, current = pending.pop()
        body = _native_bytes(document)
        if body is None:
            return None
        signature.update(body)
        nodes = [document]
        while nodes:
            node = nodes.pop()
            if type(node) is list:
                nodes.extend(node)
            elif type(node) is dict:
                if "$dynamicRef" in node or "$recursiveRef" in node:
                    return None
                if "$id" in node:
                    # An embedded new base needs an independent complete
                    # resolver walk; retain the normal validator for that shape.
                    return None
                reference = node.get("$ref")
                if reference is not None:
                    if type(reference) is not str:
                        return None
                    effective = current._registry  # pyright: ignore[reportPrivateUsage]
                    base = current._base_uri  # pyright: ignore[reportPrivateUsage]
                    retrieve = effective._retrieve  # pyright: ignore[reportPrivateUsage]
                    if retrieve is not _deny_retrieve and retrieve is not _DEFAULT_RETRIEVE:
                        return None
                    uri = urldefrag(urljoin(base, reference))[0]
                    if uri not in effective:
                        return None  # Never invoke retrieval for memo bookkeeping.
                    # crawl() may create a fresh immutable Registry each lookup;
                    # its id is not semantic identity. Exact reachable bodies
                    # are fingerprinted after resolution, including replacements.
                    identity = (base, reference)
                    if identity not in seen:
                        if len(seen) >= 64:
                            return None
                        seen.add(identity)
                        found = _snapshot_lookup(current, reference)
                        if found is None:
                            return None
                        contents, following = found
                        signature.update(repr(identity).encode())
                        pending.append((contents, following))
                nodes.extend(node.values())
    return signature.digest()


def _snapshot_header(validator: Draft202012Validator) -> tuple[Any, ...] | None:
    """Fresh checks of the same pure validator extensions, without schema JSON."""
    if not _reference_operations_unchanged() or (
        type(validator) is not Draft202012Validator
        or Draft202012Validator.validate is not _DRAFT_VALIDATE
        or validator.TYPE_CHECKER is not _DRAFT_TYPE_CHECKER
        or type(validator.VALIDATORS) is not dict
        or len(validator.VALIDATORS) != len(_DRAFT_KEYWORDS)
        or any(
            type(name) is not str or function is not _DRAFT_KEYWORDS.get(name)
            for name, function in validator.VALIDATORS.items()
        )
    ):
        return None
    registry = validator._registry  # pyright: ignore[reportPrivateUsage]
    resolver = validator._resolver  # pyright: ignore[reportPrivateUsage]
    if (
        type(registry) is not Registry
        or type(resolver) is not _RESOLVER_TYPE
        or validator._ref_resolver is not None  # pyright: ignore[reportPrivateUsage]
    ):
        return None
    retrieve = registry._retrieve  # pyright: ignore[reportPrivateUsage]
    effective = resolver._registry  # pyright: ignore[reportPrivateUsage]
    base = resolver._base_uri  # pyright: ignore[reportPrivateUsage]
    if type(effective) is not Registry or type(base) is not str:
        return None
    effective_retrieve = effective._retrieve  # pyright: ignore[reportPrivateUsage]
    if (
        retrieve is not _deny_retrieve
        and retrieve is not _DEFAULT_RETRIEVE
        or effective_retrieve is not _deny_retrieve
        and effective_retrieve is not _DEFAULT_RETRIEVE
    ):
        return None
    header: list[Any] = [id(registry), id(resolver), id(validator.TYPE_CHECKER), base]
    checker = validator.format_checker
    if checker is None:
        header.append(None)
    else:
        if (
            type(checker) is not FormatChecker
            or type(checker.checkers) is not dict
            or any(type(name) is not str for name in checker.checkers)
        ):
            return None
        header.append(id(checker))
        for name, pair in sorted(checker.checkers.items()):
            if type(pair) is not tuple or len(pair) != 2:
                return None
            function, raises = pair
            if not _pure_format(function, raises):
                return None
            exceptions = (
                cast(tuple[type[Exception], ...], raises) if type(raises) is tuple else (raises,)
            )
            header.append(
                (
                    name,
                    id(function),
                    id(getattr(function, "__code__", None)),
                    tuple(id(exception) for exception in exceptions),
                )
            )
    bindings = validator._validators  # pyright: ignore[reportPrivateUsage]
    if type(bindings) is not list:
        return None
    for binding in bindings:
        if type(binding) is not tuple or len(binding) != 3:
            return None
        function, keyword, _bound = binding
        if type(keyword) is not str or function is not _DRAFT_KEYWORDS.get(keyword):
            return None
        header.append((keyword, id(function)))
    return tuple(header)


def _reference_operations_unchanged() -> bool:
    return all(
        _same_reference_callable(getattr(owner, name, None), function)
        and getattr(function, "__code__", None) is code
        for owner, name, function, code in _REFERENCE_OPERATIONS
    ) and all(
        function.__code__ is code
        and function.__globals__ is namespace
        and function.__defaults__ is defaults
        and function.__kwdefaults__ is kwdefaults
        and function.__closure__ is closure
        for function, code, namespace, defaults, kwdefaults, closure in (
            *_DRAFT_FUNCTION_METADATA,
            *_REFERENCE_FUNCTION_METADATA,
        )
    )


def _same_reference_callable(current: Any, original: Any) -> bool:
    # Specification.anchors_in is a bound method: attribute access creates a
    # new wrapper, while its actual descriptor and instance stay identical.
    # Replacement functions, owners and unknown callable objects still bypass.
    if type(original) is MethodType:
        return (
            type(current) is MethodType
            and current.__func__ is original.__func__
            and current.__self__ is original.__self__
        )
    return current is original


class _SnapshotLimit(Exception):
    pass


def _copy_native(value: Any, budget: list[int], *, depth: int = 0) -> Any:
    """A private deep copy with exact native types and accounted Python bytes."""
    if depth > 64:
        raise _SnapshotLimit
    kind = type(value)
    if kind is dict:
        result: Any = {}
        for key, child in value.items():
            if type(key) is not str:
                raise _SnapshotLimit
            budget[0] -= sys.getsizeof(key)
            result[key] = _copy_native(child, budget, depth=depth + 1)
    elif kind is list:
        result = [_copy_native(child, budget, depth=depth + 1) for child in value]
    elif kind is str or kind is int or kind is bool or kind is type(None):
        result = value
    elif kind is float and math.isfinite(value):
        result = value
    else:
        raise _SnapshotLimit
    budget[0] -= sys.getsizeof(result)
    if budget[0] < 0:
        raise _SnapshotLimit
    return result


def _same_native(current: Any, snapshot: Any, *, depth: int = 0) -> bool:
    """Never call subclass/custom equality or collapse bool/int and tuple/list."""
    if depth > 64 or type(current) is not type(snapshot):
        return False
    kind = type(current)
    if kind is dict:
        if (
            len(current) != len(snapshot)
            or any(type(key) is not str for key in current)
            or any(type(key) is not str for key in snapshot)
        ):
            return False
        return all(
            key in snapshot and _same_native(child, snapshot[key], depth=depth + 1)
            for key, child in current.items()
        )
    if kind is list:
        return len(current) == len(snapshot) and all(
            _same_native(left, right, depth=depth + 1)
            for left, right in zip(current, snapshot, strict=True)
        )
    if kind is str or kind is int or kind is bool or kind is type(None):
        return current == snapshot
    if kind is float:
        return math.isfinite(current) and current.hex() == snapshot.hex()
    return False


def _metadata_bytes(value: Any) -> int:
    if type(value) is tuple:
        return sys.getsizeof(value) + sum(_metadata_bytes(child) for child in value)
    return sys.getsizeof(value)


def _snapshot_lookup(current: Any, reference: str) -> tuple[Any, Any] | None:
    """Only a current registered Draft 2020-12 JSON-pointer lookup, never I/O."""
    if type(current) is not _RESOLVER_TYPE or type(reference) is not str:
        return None
    effective = current._registry  # pyright: ignore[reportPrivateUsage]
    base = current._base_uri  # pyright: ignore[reportPrivateUsage]
    if type(effective) is not Registry or type(base) is not str:
        return None
    retrieve = effective._retrieve  # pyright: ignore[reportPrivateUsage]
    if retrieve is not _deny_retrieve and retrieve is not _DEFAULT_RETRIEVE:
        return None
    uri, fragment = urldefrag(urljoin(base, reference))
    if uri not in effective or fragment and not fragment.startswith("/"):
        return None
    resource = effective[uri]
    if (
        type(resource) is not Resource or resource._specification is not DRAFT202012  # pyright: ignore[reportPrivateUsage]
    ):
        return None
    resolved = current.lookup(reference)
    if type(resolved.resolver) is not _RESOLVER_TYPE:
        return None
    return resolved.contents, resolved.resolver


def _capture_schema_snapshot(
    validator: Draft202012Validator,
    signature: bytes,
    budget: int,
) -> _SchemaSnapshot | None:
    """Called only after an actual successful pure shape check in this scope."""
    try:
        header = _snapshot_header(validator)
        if header is None or budget <= 512:
            return None
        remaining = [budget - 512]
        bindings = tuple(
            (keyword, _copy_native(bound, remaining))
            for _function, keyword, bound in validator._validators  # pyright: ignore[reportPrivateUsage]
        )
        resolver = validator._resolver  # pyright: ignore[reportPrivateUsage]
        documents = [_copy_native(validator.schema, remaining)]
        bases = [resolver._base_uri]  # pyright: ignore[reportPrivateUsage]
        references: list[_SchemaReference] = []
        pending = [(0, resolver)]
        seen: dict[tuple[str, str], int] = {}
        while pending:
            parent, current = pending.pop()
            nodes = [documents[parent]]
            parent_refs: set[str] = set()
            while nodes:
                node = nodes.pop()
                if type(node) is list:
                    nodes.extend(node)
                elif type(node) is dict:
                    if "$dynamicRef" in node or "$recursiveRef" in node or "$id" in node:
                        return None
                    reference = node.get("$ref")
                    if reference is not None:
                        if type(reference) is not str:
                            return None
                        if reference not in parent_refs:
                            parent_refs.add(reference)
                            found = _snapshot_lookup(current, reference)
                            if found is None or len(references) >= 256:
                                return None
                            document, following = found
                            base = current._base_uri  # pyright: ignore[reportPrivateUsage]
                            following_base = following._base_uri  # pyright: ignore[reportPrivateUsage]
                            if type(following_base) is not str:
                                return None
                            identity = (base, reference)
                            target = seen.get(identity)
                            if target is None:
                                if len(seen) >= 64:
                                    return None
                                target = len(documents)
                                seen[identity] = target
                                documents.append(_copy_native(document, remaining))
                                bases.append(following_base)
                                pending.append((target, following))
                            elif following_base != bases[target] or not _same_native(
                                document, documents[target]
                            ):
                                return None
                            references.append(_SchemaReference(parent, target, reference))
                            remaining[0] -= sys.getsizeof(references[-1]) + sys.getsizeof(reference)
                            if remaining[0] < 0:
                                return None
                    nodes.extend(node.values())
        # Header identities are primitive metadata; account its container too.
        remaining[0] -= _metadata_bytes(header)
        remaining[0] -= sum(sys.getsizeof(pair) + sys.getsizeof(pair[0]) for pair in bindings)
        remaining[0] -= sum(sys.getsizeof(base) for base in bases)
        remaining[0] -= sum(
            sys.getsizeof(value) for value in (bindings, documents, bases, references)
        )
        if remaining[0] < 0 or _snapshot_header(validator) != header:
            return None
        return _SchemaSnapshot(
            validator,
            signature,
            header,
            bindings,
            tuple(documents),
            tuple(bases),
            tuple(references),
            budget - remaining[0],
        )
    except Exception:
        return None  # An optional snapshot never changes validation or its error.


def _schema_snapshot_matches(
    validator: Draft202012Validator,
    snapshot: _SchemaSnapshot,
    header: tuple[Any, ...],
) -> bool:
    try:
        if validator is not snapshot.validator or header != snapshot.header:
            return False
        bindings = validator._validators  # pyright: ignore[reportPrivateUsage]
        if len(bindings) != len(snapshot.bindings) or any(
            keyword != saved_keyword or not _same_native(bound, saved_bound)
            for (_function, keyword, bound), (saved_keyword, saved_bound) in zip(
                bindings, snapshot.bindings, strict=True
            )
        ):
            return False
        if not _same_native(validator.schema, snapshot.documents[0]):
            return False
        resolvers: dict[int, Any] = {
            0: validator._resolver,  # pyright: ignore[reportPrivateUsage]
        }
        # Parent resolvers are obtained from this call, not retained old lookups.
        for edge in snapshot.references:
            found = _snapshot_lookup(resolvers[edge.parent], edge.reference)
            if found is None:
                return False
            document, following = found
            if (
                following._base_uri != snapshot.bases[edge.target]  # pyright: ignore[reportPrivateUsage]
                or not _same_native(document, snapshot.documents[edge.target])
            ):
                return False
            resolvers.setdefault(edge.target, following)
        return True
    except Exception:
        return False


def _memo_schema_signature(validator: Draft202012Validator, memo: _ShapeMemo) -> bytes | None:
    try:
        header = _snapshot_header(validator)
    except Exception:
        header = None
    snapshot = memo.schemas.get(id(validator))
    if snapshot is not None:
        if header is not None and _schema_snapshot_matches(validator, snapshot, header):
            memo.stats.schema_snapshot_hit_count += 1
            return snapshot.signature
        del memo.schemas[id(validator)]
        memo.retained_bytes -= snapshot.retained_bytes
    if header is None:
        return None
    return _shape_schema_signature(validator)


def _validate_shape(
    model: type[BaseModel],
    validator: Draft202012Validator,
    projected: Any,
) -> None:
    memo = _SHAPE_MEMO.get()
    if memo is None or not _owned(memo):
        validator.validate(projected)
        return
    if _rule_discriminator_mismatch(validator, projected):
        memo.stats.miss_count += 1
        memo.stats.fast_reject_count += 1
        raise JsonSchemaValidationError("rule_id differs from the required canonical rule tag")
    payload = (
        _native_bytes(projected, MAX_SHAPE_MEMO_VALUE_BYTES)
        if (type(model) is type(BaseModel))
        else None
    )
    schema = _memo_schema_signature(validator, memo) if payload is not None else None
    if schema is None or payload is None:
        memo.stats.bypass_count += 1
        validator.validate(projected)
        return
    key = (model, id(validator), schema, payload)
    if key in memo.successes:
        memo.stats.hit_count += 1
        return
    memo.stats.miss_count += 1
    validator.validate(projected)  # Failures are never inserted.
    retained = len(payload) + len(schema) + 128
    if (
        len(memo.successes) < MAX_SHAPE_MEMO_ENTRIES
        and memo.retained_bytes + retained <= MAX_SHAPE_MEMO_TOTAL_BYTES
    ):
        memo.successes.add(key)
        memo.retained_bytes += retained
        if id(validator) not in memo.schemas and len(memo.schemas) < MAX_SHAPE_MEMO_ENTRIES:
            snapshot = _capture_schema_snapshot(
                validator,
                schema,
                MAX_SHAPE_MEMO_TOTAL_BYTES - memo.retained_bytes,
            )
            if snapshot is not None:
                memo.schemas[id(validator)] = snapshot
                memo.retained_bytes += snapshot.retained_bytes


def _json_input(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("research_binding_datetime_must_be_aware")
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_input(value.model_dump(mode="json"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("research_binding_non_string_key")
        return {key: _json_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_input(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("research_binding_non_finite_number")
    return value


def _deny_retrieve(uri: str) -> Resource[Any]:
    raise NoSuchResource(ref=uri)


@lru_cache(maxsize=1)
def binding_schema_registry() -> tuple[dict[str, dict[str, Any]], Registry[Any]]:
    """Never fetch arbitrary $ref locations, including file/network schemes."""
    root = Path(__file__).resolve().parents[3] / "contracts/schemas"
    documents: dict[str, dict[str, Any]] = {}
    registry: Registry[Any] = Registry(retrieve=_deny_retrieve)
    for name in SCHEMA_FILES:
        with (root / name).open("rb") as stream:
            raw = stream.read(MAX_SCHEMA_BYTES + 1)
        if len(raw) > MAX_SCHEMA_BYTES:
            raise ValueError("research_binding_canonical_schema_size_invalid")
        document = yaml.safe_load(raw.decode("utf-8", errors="strict"))
        Draft202012Validator.check_schema(document)
        documents[name] = document
        resource = Resource.from_contents(document, default_specification=DRAFT202012)
        registry = registry.with_resource(document["$id"], resource).with_resource(
            urljoin(document["$id"], name), resource
        )
    return documents, registry


def binding_validator(definition: str) -> Draft202012Validator:
    documents, registry = binding_schema_registry()
    schema = documents[SCHEMA_FILES[0]]
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{definition}"},
        registry=registry,
        format_checker=policy_format_checker(),
    )


@lru_cache(maxsize=128)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    documents, registry = binding_schema_registry()
    for document in documents.values():
        for key, definition in document.get("$defs", {}).items():
            title = definition.get("title") or "".join(part.title() for part in key.split("_"))
            if title == model.__name__:
                return Draft202012Validator(
                    {"$ref": f"{document['$id']}#/$defs/{key}"},
                    registry=registry,
                    format_checker=policy_format_checker(),
                )
    # Anonymous generated params are still checked by their original parent.
    return Draft202012Validator(model.model_json_schema(), format_checker=policy_format_checker())


class ResearchBindingContract(BaseModel):
    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        projected = _json_input(value)
        try:
            _validate_shape(cls, _validator(cls), projected)
        except JsonSchemaValidationError as exc:
            raise ValueError("research_binding_contract_invalid") from exc
        # datamodel-codegen may inline canonical dependencies. Preserve the
        # original shape when callers pass their existing typed DTO instances.
        return projected
