"""Pure query controls; no DB/HTTP/Root or model execution."""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.query_views.research.recent import recent_research_runs


@pytest.fixture(autouse=True)
def raw_read_seam(monkeypatch):
    # This fixture controls only the existing list selection boundary. The batch
    # reader and original view equivalence have their own independent controls.
    monkeypatch.setattr("packages.query_views.research.recent._read_recent_run_rows",
                        lambda session, rows: {row.run_id: row for row in rows})


class Query:
    def __init__(self, rows):
        self.rows, self.bounded, self.filtered, self.ordered = rows, None, False, False
    def filter(self, expression):
        assert expression.compare(RunRecord.strategy_version == "research.v1")
        self.filtered = True
        return self
    def order_by(self, first, second):
        assert first.compare(RunRecord.updated_at.desc())
        assert second.compare(RunRecord.run_id.asc())
        self.ordered = True
        return self
    def limit(self, value):
        self.bounded = value
        return self
    def all(self):
        assert self.filtered and self.ordered and self.bounded is not None
        rows = [r for r in self.rows if r.strategy_version == "research.v1"]
        return sorted(rows, key=lambda r: (-r.updated_at.timestamp(), r.run_id))[:self.bounded]


def service(rows):
    query, viewed = Query(rows), []
    session = SimpleNamespace(query=lambda model: query if model is RunRecord else pytest.fail("wrong model"))
    @contextmanager
    def scope():
        yield session
    def original_view(actual_session, row, *, _read_rows=None):
        assert actual_session is session and query.bounded <= 8
        assert _read_rows is row
        viewed.append(row)
        return {"run_id": row.run_id, "original_view": True}
    return SimpleNamespace(database=SimpleNamespace(session=scope), _run_view=original_view), query, viewed


def row(run, day, strategy="research.v1", status="failed"):
    return SimpleNamespace(run_id=run, updated_at=datetime(2026, 10, day, tzinfo=timezone.utc),
                           strategy_version=strategy, status=status)


def test_limit_before_original_projection_uses_recent_not_priority_and_stable_tie():
    rows = [row(f"old-{i}", 1, status="researching") for i in range(100)]
    rows += [row(f"new-{i}", 10) for i in reversed(range(10))]
    rows += [row("non-research", 11, strategy="decision.v1")]
    actual, query, viewed = service(rows)
    result = recent_research_runs(actual)
    assert [v["run_id"] for v in result] == [f"new-{i}" for i in range(8)]
    assert len(viewed) == 8 and query.bounded == 8
    assert all(v["original_view"] for v in result)
    assert len(rows) == 111


@pytest.mark.parametrize("limit", [0, 9, True, "8"])
def test_limit_refuses_expanded_or_untyped_reads(limit):
    actual, query, viewed = service([])
    with pytest.raises(ValueError, match="research_recent_limit_invalid"):
        recent_research_runs(actual, limit=limit)
    assert query.bounded is None and not viewed


def test_missing_rows_remain_empty_without_publishing_or_filter_fallback():
    actual, query, viewed = service([row("not-research", 11, strategy="decision.v1")])
    assert recent_research_runs(actual) == []
    assert query.bounded == 8 and not viewed


def test_route_and_old_business_order_cursor_semantics_are_exactly_preserved():
    root = Path(__file__).resolve().parents[2]
    before = root / "artifacts/core-goal-20261011/research-recent-list01/before/apps/hub_api/main.py"
    after = root / "apps/hub_api/main.py"
    def routes(path):
        tree = ast.parse(path.read_text())
        return {n.name: ast.dump(n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    a, b = routes(before), routes(after)
    for name, body in a.items():
        assert b[name] == body if name != "create_app" else True
    assert "research_recent_runs" in b
    text = after.read_text()
    assert '"/v1/research/recent-runs", response_model=list[ResearchRunView]' in text
    assert "limit: int = Query(default=8, ge=1, le=8)" in text
