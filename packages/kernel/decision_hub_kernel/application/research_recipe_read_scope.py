"""Completed recipe originals reused within one callback's clean physical read.

This is not a permission cache. The first read and the scope's final verification
use the original reader; the caller's source/readset and writer fences stay intact.
No source guard is captured here. Values are canonical bytes and every return is
a newly parsed typed value, never a shared mutable model.
"""

from __future__ import annotations

import asyncio
import threading
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..persistence.operation_phases import OperationPhases


@dataclass(frozen=True)
class _CompletedRecipeRead:
    body: str
    verify: Callable[[], str]
    kind: str


class _RecipeReadScope:
    def __init__(self, session: Session, boundary: Any) -> None:
        self.session, self.boundary = session, boundary
        self.owner = _execution_owner()
        self.values: dict[tuple[Any, ...], _CompletedRecipeRead] = {}

    def owns_execution(self) -> bool:
        return self.owner == _execution_owner()

    def require_unchanged(self) -> None:
        if (
            not self.owns_execution()
            or not self.boundary.unchanged()
            or self.boundary.connection.connection.driver_connection is not self.boundary.driver
        ):
            raise ValueError("research_graph_recipe_read_scope_changed")

    def completed[T](
        self,
        session: Session,
        key: tuple[Any, ...],
        read: Callable[[], T],
        encode: Callable[[T], str],
        decode: Callable[[str], T],
        *,
        kind: str,
        cacheable: Callable[[T], bool] = lambda value: True,
    ) -> T:
        if session is not self.session or not self.owns_execution():
            token = _CURRENT.set(None)
            try:
                return read()
            finally:
                _CURRENT.reset(token)
        self.require_unchanged()
        original = self.values.get(key)
        if original is not None:
            # Validate the full canonical payload on every returned clone.
            return decode(original.body)
        value = read()
        self.require_unchanged()
        if cacheable(value) and len(self.values) < 32:
            body = encode(value)

            def verify() -> str:
                fresh = read()
                if not cacheable(fresh):
                    raise ValueError("research_graph_recipe_read_original_changed")
                return encode(fresh)

            self.values[key] = _CompletedRecipeRead(body, verify, kind)
            return decode(body)
        return value

    def verify(self) -> None:
        self.require_unchanged()
        # Each exact current selection's original reader freshly authenticates
        # this same installation at the end. A standalone/different installation
        # is never covered by another selection's verification.
        counts = Counter(
            (key[1], key[-1]) for key, value in self.values.items()
            if value.kind == "selection"
        )
        seeds = {
            key: value for key, value in self.values.items()
            if value.kind == "installation" and counts[key[1], key[-1]] > 1
        }
        eligible = {(key[1], key[-1]) for key in seeds}
        if eligible:
            # Selection bodies and every proof ancestry still use the original
            # reader. Their installation has no ancestry: use only the source
            # proof already fully authenticated in this same physical read,
            # then freshly authenticate the full installation again at exit.
            final = _InstallationVerificationScope(self.session, self.boundary, eligible)
            self.require_unchanged()
            final.require_unchanged()
            final.values.update(seeds)
            self.require_unchanged()
            final.require_unchanged()
            token = _CURRENT.set(final)
            try:
                self._verify_values(set(counts))
                _CURRENT.reset(token)
                token = _CURRENT.set(None)
                final.verify()
                self.require_unchanged()
            finally:
                final.values.clear()
                _CURRENT.reset(token)
            return
        self._verify_values(set(counts))

    def _verify_values(self, covered: set[tuple[Any, ...]]) -> None:
        for key, value in self.values.items():
            if value.kind == "installation" and (key[1], key[-1]) in covered:
                continue
            if value.verify() != value.body:
                raise ValueError("research_graph_recipe_read_original_changed")
            self.require_unchanged()


class _InstallationVerificationScope(_RecipeReadScope):
    """Exact source proof reuse only inside one physical final verification."""

    def __init__(self, session: Session, boundary: Any, eligible: set[tuple[Any, ...]]) -> None:
        super().__init__(session, boundary)
        self.eligible = eligible

    def completed[T](
        self,
        session: Session,
        key: tuple[Any, ...],
        read: Callable[[], T],
        encode: Callable[[T], str],
        decode: Callable[[str], T],
        *,
        kind: str,
        cacheable: Callable[[T], bool] = lambda value: True,
    ) -> T:
        if session is not self.session or not self.owns_execution():
            return super().completed(
                session, key, read, encode, decode, kind=kind, cacheable=cacheable,
            )
        if kind != "installation" or (key[1], key[-1]) not in self.eligible:
            self.require_unchanged()
            value = read()
            self.require_unchanged()
            return value
        return super().completed(
            session, key, read, encode, decode, kind=kind, cacheable=cacheable,
        )


_CURRENT: ContextVar[_RecipeReadScope | None] = ContextVar(
    "research_recipe_original_read", default=None
)


def _execution_owner() -> tuple[int, object | None]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def current_recipe_read_scope() -> _RecipeReadScope | None:
    scope = _CURRENT.get()
    return scope if scope is not None and scope.owns_execution() else None


@contextmanager
def recipe_original_read_scope(
    session: Session, *, diagnostics: OperationPhases | None = None,
) -> Generator[None]:
    """Only the explicitly scoped callback RO preparation opts into reuse.

    No cache crosses a scope, Session, transaction, flush, write or proof ancestry.
    Final checks run with reuse disabled, before the outer original read ends.
    """
    from .research_graph_binding import _local_original_read  # pyright: ignore[reportPrivateUsage]

    phases = diagnostics or OperationPhases()
    with _local_original_read(session) as boundary:
        scope = _RecipeReadScope(session, boundary)
        scope.require_unchanged()
        token = _CURRENT.set(scope)
        try:
            with session.no_autoflush:
                yield
                _CURRENT.reset(token)
                token = _CURRENT.set(None)
                with phases.phase("auth_recipe_exit"):
                    scope.verify()
        finally:
            scope.values.clear()
            _CURRENT.reset(token)
