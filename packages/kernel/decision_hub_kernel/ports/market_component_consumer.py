"""Trusted pure component consumption inside the caller's physical transaction."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from .component_registry import ComponentKind


class MarketComponentConsumer(Protocol):
    def __call__(
        self,
        session: Session,
        *,
        kind: ComponentKind,
        component_ref: str,
        scope_id: str,
        request_id: str,
        known_at: datetime,
        payload: dict[str, Any],
        fallback: Callable[[dict[str, Any]], Any],
        cutoff_at: datetime | None = None,
    ) -> tuple[Any, dict[str, Any]]: ...
