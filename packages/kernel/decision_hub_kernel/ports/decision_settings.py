"""Settings binding boundary; a pending hint grants no execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsCommandV1,
)


class BindingReader(Protocol):
    """Authenticate instrument/profile and owner/account originals in this read snapshot.

    Paper bindings must read the registered portfolio and authentic account original;
    callers cannot substitute a client supplied paper balance. Every database read is
    fenced by the existing prepare_originals readset before the writer commits.
    """

    def __call__(
        self, session: Session, owner: str, command: DecisionSettingsCommandV1
    ) -> None: ...


@dataclass(frozen=True)
class PendingSettings:
    settings_key: str
    revision_id: str
    revision_hash: str
    state_version: int
    scan_generation: int
    scan_after_plan_id: str | None
