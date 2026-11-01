"""Dispatch routing only; retained authenticity is exercised by the same-DB release gate."""

from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application import research_graph_binding as graph
from packages.kernel.decision_hub_kernel.application import research_perpetual_graph


class RetainedSelected(Exception):
    pass


@pytest.mark.parametrize("kind", ["round", "final"])
def test_terminal_perpetual_read_reaches_existing_retained_reader(monkeypatch, kind):
    monkeypatch.setattr(
        graph, "_perpetual_saved_terminal", lambda _session, _run: True, raising=False
    )

    def retained(*_args, **_kwargs):
        raise RetainedSelected

    def current(*_args, **_kwargs):
        raise AssertionError("current perpetual reader selected before retained")

    monkeypatch.setattr(graph, "replay_retained_graph_" + kind + "_in_session", retained)
    monkeypatch.setattr(research_perpetual_graph, "read_" + kind, current)
    schema = graph.PERPETUAL_ROUND_SCHEMA if kind == "round" else graph.PERPETUAL_FINAL_SCHEMA
    ref = SimpleNamespace(row=SimpleNamespace(schema_version=schema), model_dump_json=lambda: "{}")
    with pytest.raises(RetainedSelected):
        getattr(graph, "read_graph_" + kind + "_in_session")(
            None,
            run_id="terminal",
            reference=ref,
            expected_lease_owner=None,
            require_current_permission=False,
        )
