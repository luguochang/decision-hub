"""The extra projection guard only; actual V3 joint proof is a separate existing node."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding


def test_v3_quality_projection_refuses_base_run_mismatch_before_any_original_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("base mismatch must not read another Run or grant preparation")

    monkeypatch.setattr(binding, "_context_valid", forbidden)
    monkeypatch.setattr(binding, "build_server_quality_context", forbidden)
    with pytest.raises(ValueError, match="^research_graph_round_observation_mismatch$"):
        binding._project_observed_v3(  # pyright: ignore[reportPrivateUsage]
            cast(Session, None),
            state=cast(Any, {}),
            base=cast(Any, SimpleNamespace(run_id="original-base")),
            actual=cast(Any, None),
            observation_ref=cast(Any, None),
            mapped=cast(Any, SimpleNamespace(context=SimpleNamespace(run_id="foreign-run"))),
            evidence=[], facts=[], admitted=[], previous=None, active=False,
            selection=cast(Any, None), assessed_at=cast(Any, None), originals=None,
        )
