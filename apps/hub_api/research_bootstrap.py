"""Initialize pure research codecs before the live callback API is ready."""
from __future__ import annotations

import logging
import time

LOGGER = logging.getLogger("uvicorn.error")


def prewarm_research_metadata() -> dict[str, float]:
    """Fill existing static caches only; never retain a Run or source guard."""
    started = time.perf_counter()
    phase = "imports"
    timings: dict[str, float] = {}
    try:
        from packages.contracts_py.decision_hub_contracts import (
            context_execution_contract_base as context_execution,
        )
        from packages.contracts_py.decision_hub_contracts import (
            context_selection_v3_contract_base as selection,
        )
        from packages.contracts_py.decision_hub_contracts import (
            execution_contract_base as execution,
        )
        from packages.contracts_py.decision_hub_contracts.generated.research_context_execution import (  # noqa: E501
            ResearchExecutionMethodReceiptV3,
        )
        from packages.contracts_py.decision_hub_contracts.generated.research_context_selection_v3 import (  # noqa: E501
            ResearchContextSelectionReceiptV3,
        )
        from packages.contracts_py.decision_hub_contracts.generated.research_execution_binding import (  # noqa: E501
            ResearchExecutionMethodReceiptV2,
        )
        from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
            _ReadSourceGuard,  # pyright: ignore[reportPrivateUsage]
        )
        from packages.kernel.decision_hub_kernel.application.research_graph_recipe import (
            graph_recipe_manifest_v2,
        )

        timings[phase] = (time.perf_counter() - started) * 1000
        # These original lru_cache entries hold local schemas and validators,
        # never request data, authenticated originals, or execution rights.
        for phase, initialize in (
            ("selection_registry", selection.context_selection_v3_schema_registry),
            ("context_execution_registry", context_execution.context_execution_registry),
            ("execution_registry", execution.execution_schema_registry),
            ("selection_validator", lambda: selection._validator(  # pyright: ignore[reportPrivateUsage]
                ResearchContextSelectionReceiptV3)),
            ("context_execution_validator", lambda: context_execution._validator(  # pyright: ignore[reportPrivateUsage]
                ResearchExecutionMethodReceiptV3)),
            ("execution_validator", lambda: execution._validator(  # pyright: ignore[reportPrivateUsage]
                ResearchExecutionMethodReceiptV2)),
            ("graph_manifest", graph_recipe_manifest_v2),
        ):
            phase_started = time.perf_counter()
            initialize()
            timings[phase] = (time.perf_counter() - phase_started) * 1000

        phase = "source_guard"
        phase_started = time.perf_counter()
        guard = _ReadSourceGuard.capture()
        if guard is None:
            raise ValueError("research_metadata_source_unavailable")
        if not guard.matches():
            raise ValueError("research_metadata_source_changed")
        del guard
        timings[phase] = (time.perf_counter() - phase_started) * 1000
    except Exception as error:
        LOGGER.error(
            "research_metadata_prewarm_failed elapsed_ms=%.3f phase=%s error_type=%s",
            (time.perf_counter() - started) * 1000, phase, type(error).__name__,
        )
        raise
    timings["total"] = (time.perf_counter() - started) * 1000
    LOGGER.info("research_metadata_prewarm_complete elapsed_ms=%.3f stages=%d",
                timings["total"], len(timings) - 1)
    return timings
