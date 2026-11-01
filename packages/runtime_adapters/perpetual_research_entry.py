"""Shared API/worker construction for the existing prepared perpetual entry.

Construction reads local configuration only. It neither admits research nor
claims Host readiness, method activation, financial qualification or execution.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from packages.contracts_py.decision_hub_contracts import DomainPackManifest
from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import OKXPerpetualSource
from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    PerpetualResearchBindingService,
)
from packages.kernel.decision_hub_kernel.application.perpetual_research_entry import (
    PerpetualResearchEntryService,
)
from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import (
    PERPETUAL_RESEARCH_RUNTIME_SECONDS,
)
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.provider_adapters.research.raw_capture import RawBodyArchive


@dataclass(frozen=True)
class PerpetualResearchServices:
    source: OKXPerpetualSource
    binding: PerpetualResearchBindingService
    entry: PerpetualResearchEntryService


def configured_perpetual_research_services(database, *, source=None, now=utcnow):
    root = Path(__file__).resolve().parents[2]
    DomainPackManifest.model_validate(
        yaml.safe_load((root / "packs/crypto_macro_prepared/1.1.0/pack.yaml").read_text("utf-8"))
    )
    actual_source = source or OKXPerpetualSource(
        database,
        RawBodyArchive(
            Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "raw-captures"
        ),
    )
    binding = PerpetualResearchBindingService(database, actual_source, now=now)
    entry = PerpetualResearchEntryService(
        database,
        binding,
        total_deadline_seconds=PERPETUAL_RESEARCH_RUNTIME_SECONDS,
        now=now,
    )
    return PerpetualResearchServices(actual_source, binding, entry)
