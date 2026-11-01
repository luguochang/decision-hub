"""Bound only pure CPI parsing, never provenance, originals or permissions.

One shared cache for CPI PDF/HTML/history parsers: four entries, each input <= 5 MiB,
therefore at most 20 MiB of retained input bytes. Each immutable result has a
2 MiB accounted object-size / 100,000-node limit; larger results bypass caching.
The bytes themselves are the key, not a caller-supplied hash or file path.
"""
from __future__ import annotations

import io
import sys
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import cast

import pypdf
from pypdf import PageObject

MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_RESULT_NODES = 100_000


class _UncachedResult(Exception):
    def __init__(self, value):
        self.value = value


def _bounded_immutable_result(value: tuple) -> bool:
    remaining, seen, size = [value], set(), 0
    while remaining:
        item = remaining.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if type(item) not in {tuple, str, bytes, int, bool, float, Decimal, date, datetime, type(None)}:
            return False
        size += sys.getsizeof(item)
        if size > MAX_RESULT_BYTES or len(seen) > MAX_RESULT_NODES:
            return False
        if type(item) is tuple:
            remaining.extend(item)
    return True


def _callable_identity(value: object) -> tuple[object, object]:
    return value, getattr(value, '__code__', None)


@lru_cache(maxsize=4)
def _cached_parse(
    body: bytes | tuple[bytes, ...],
    parser_version: str,
    parser_identity: tuple[object, object],
    dependencies: tuple[object, ...],
    parameters: tuple[object, ...] = (),
) -> tuple:
    # Exceptions are deliberately not memoized by functools.lru_cache.
    parser = cast(Callable[..., tuple], parser_identity[0])
    bodies = body if type(body) is tuple else (body,)
    result = parser(*bodies, *parameters)
    if not _bounded_immutable_result(result):
        raise _UncachedResult(result)
    return result


def cached_cpi_parse[T: tuple](
    bodies: tuple[bytes, ...], parser: Callable[..., T], *, parser_version: str,
    parameters: tuple[object, ...] = (), dependencies: tuple[object, ...] = (),
) -> T:
    # Preserve accepted legacy inputs and oversized history semantics. The
    # optimization only retains actual immutable bytes, never a supplied hash.
    if (any(type(body) is not bytes for body in bodies)
            or sum(len(body) for body in bodies) > MAX_BODY_BYTES):
        return parser(*bodies, *parameters)
    identities = tuple(_callable_identity(value) if callable(value) else value
                       for value in dependencies)
    try:
        return cast(T, _cached_parse(
            bodies, parser_version, _callable_identity(parser), identities, parameters,
        ))
    except _UncachedResult as uncached:
        return uncached.value


def cached_cpi_pdf_parse[T: tuple](
    body: bytes,
    parser: Callable[[bytes], T],
    *,
    parser_version: str,
    reader: object,
    dependencies: tuple[object, ...] = (),
) -> T:
    immutable_body = bytes(body)
    if len(immutable_body) > MAX_BODY_BYTES:
        raise ValueError('cpi_pdf_cache_body_too_large')
    dependency_identity = (
        pypdf.__version__,
        _callable_identity(reader),
        _callable_identity(getattr(reader, '__init__', None)),
        _callable_identity(getattr(reader, 'read', None)),
        _callable_identity(pypdf.PdfReader),
        _callable_identity(pypdf.PdfReader.__init__),
        _callable_identity(PageObject.extract_text),
        _callable_identity(PageObject._extract_text),
        io.BytesIO,
        tuple(_callable_identity(value) if callable(value) else value for value in dependencies),
    )
    try:
        return cast(T, _cached_parse(
            immutable_body, parser_version, _callable_identity(parser), dependency_identity,
        ))
    except _UncachedResult as uncached:
        return uncached.value
