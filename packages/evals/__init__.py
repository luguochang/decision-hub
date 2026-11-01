"""Deterministic replay, holdout and shadow evaluation helpers."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .research_assets import ResearchComparisonAssetRecorder
    from .research_runtime import CaseScopedDshResearchRuntime
    from .runner import EvaluationRun, EvaluationRunner

__all__ = [
    "CaseScopedDshResearchRuntime",
    "EvaluationRunner",
    "EvaluationRun",
    "ResearchComparisonAssetRecorder",
]


def __getattr__(name: str) -> Any:
    # Loading a pure calibration schema must not start the orchestration
    # runtime. Relative imports also preserve retained private-reader imports.
    if name == "ResearchComparisonAssetRecorder":
        from .research_assets import ResearchComparisonAssetRecorder
        value = ResearchComparisonAssetRecorder
    elif name == "CaseScopedDshResearchRuntime":
        from .research_runtime import CaseScopedDshResearchRuntime
        value = CaseScopedDshResearchRuntime
    elif name in {"EvaluationRun", "EvaluationRunner"}:
        from .runner import EvaluationRun, EvaluationRunner
        globals().update(EvaluationRun=EvaluationRun, EvaluationRunner=EvaluationRunner)
        value = globals()[name]
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
