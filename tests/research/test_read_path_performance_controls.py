"""Pure query/row controls: no engine, DB, HTTP, model or product startup."""
import ast
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects import sqlite

from packages.kernel.decision_hub_kernel.application.research_value_basis import SavedCoverageBasis
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord, DshSessionLinkRecord, ObservationRecord, ResearchEvidenceRecord,
    ResearchFactRecord, ResearchResultRecord, ResearchTraceRecord, RunRecord,
)
from packages.query_views.research import recent, service as current
from packages.query_views.market_risk import MarketRiskQuery

ROOT = Path(__file__).resolve().parents[2]
BEFORE = ROOT / "artifacts/core-goal-20261011/read-path-performance59_01/before"
AT = datetime(2026, 10, 11, tzinfo=timezone.utc)


def before_module():
    name = "packages.query_views.research._read_path_before"
    spec = importlib.util.spec_from_file_location(name, BEFORE / "packages/query_views/research/service.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class Query(Rows):
    def filter_by(self, **values):
        self.rows = [r for r in self.rows if all(getattr(r, k) == v for k, v in values.items())]
        return self
    def order_by(self, *columns):
        for c in reversed(columns):
            reverse = getattr(getattr(c, "modifier", None), "__name__", "") == "desc_op"
            key = c.element.key if hasattr(c, "element") else c.key
            self.rows.sort(key=lambda r: getattr(r, key), reverse=reverse)
        return self
    def first(self): return self.rows[0] if self.rows else None


class Session:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
    def query(self, model):
        self.calls.append(("query", model.__name__))
        return Query(list(self.rows.get(model, [])))
    def get(self, model, key):
        self.calls.append(("get", model.__name__))
        column = {ResearchResultRecord: "run_id", ArtifactRecord: "artifact_id",
                  DshSessionLinkRecord: "run_id"}[model]
        return next((r for r in self.rows.get(model, []) if getattr(r, column) == key), None)
    def scalars(self, statement):
        # Compile only; never execute SQL. Exercise real entity/window/query shape.
        statement.compile(dialect=sqlite.dialect())
        entity = statement.column_descriptions[0]["entity"]
        model = inspect(entity).mapper.class_
        if model is ArtifactRecord:
            compiled = str(statement.compile(dialect=sqlite.dialect()))
            assert "artifacts.payload_json" not in compiled and "artifacts.summary" not in compiled
        self.calls.append(("batch", model.__name__))
        rows = list(self.rows.get(model, []))
        for condition in statement._where_criteria:
            if getattr(getattr(condition, "operator", None), "__name__", "") == "in_op":
                rows = [r for r in rows if getattr(r, condition.left.key) in condition.right.value]
        if model is ObservationRecord:
            # Partition is filtered inside the subquery, rank=1 outside it.
            nested = next(iter(statement.get_final_froms()))
            while hasattr(nested, "element"): nested = nested.element
            scope = nested._where_criteria[0].right.value
            rows = [r for r in rows if r.event_id in scope]
            grouped = defaultdict(list)
            for row in rows: grouped[row.event_id].append(row)
            rows = [min(group, key=lambda r: r.received_at) for group in grouped.values()]
        elif model is ResearchTraceRecord and not statement._where_criteria:
            latest = statement.get_final_froms()[0].right.element
            scope = latest._where_criteria[0].right.value
            grouped = defaultdict(list)
            for row in rows:
                if row.run_id in scope: grouped[row.run_id].append(row)
            rows = [max(group, key=lambda r: r.sequence_no) for group in grouped.values()]
        else:
            rows = Query(rows).order_by(*statement._order_by_clauses).all()
        return Rows(rows)


def run(key="run-a", status="researching", artifact=None, error=None):
    return RunRecord(run_id=key, event_id="event-" + key, strategy_version="research.v1",
                     status=status, artifact_id=artifact, error_code=error,
                     admission_origin="manual", priority=50, available_at=None,
                     parent_run_id=None, created_at=AT, updated_at=AT, finished_at=None)


def trace(key, sequence, session="fixed:fixture"):
    return ResearchTraceRecord(run_id=key, research_session_id=session, sequence_no=sequence,
                               event_type="plan_created", occurred_at=AT,
                               stage="planning", summary=f"phase {sequence}", status="succeeded",
                               reference_type=None, reference_id=None, error_code=None,
                               error_provenance_json=None)


def result(key, payload='{"valid":true}', corrupt=False):
    return ResearchResultRecord(run_id=key, schema_version="research-session-result.v1",
                                payload_json=payload, payload_hash=("0" * 64 if corrupt else
                                    hashlib.sha256(payload.encode()).hexdigest()))


def fake_saved_parser(monkeypatch):
    original = current.ResearchSessionResult.model_validate_json
    def parse(value):
        if value == '{"valid":true}':
            return SimpleNamespace(runtime_id="dsh", profile_ref="fixture.v1", rounds=[],
                                   final_coverage=None, stop_reason=None)
        return original(value)
    monkeypatch.setattr(current.ResearchSessionResult, "model_validate_json", parse)


def view_service(module, calls):
    instance = object.__new__(module.ResearchQueryService)
    def basis(session, row):
        calls.append(("coverage", session, row.run_id))
        return SavedCoverageBasis("unavailable")
    def submitted(session, row):
        calls.append(("submission", session, row.run_id))
        return SavedCoverageBasis("unavailable")
    instance._coverage_basis_for_run = basis
    instance._submission_basis_for_run = submitted
    return instance


@pytest.mark.parametrize("status,saved_kind,error", [
    ("researching", "missing", None), ("completed", "valid", None),
    ("completed", "corrupt", None), ("failed", "missing", "worker_unavailable"),
    ("cancelled", "missing", "cancelled_by_owner"), ("degraded", "valid", "worker_unavailable"),
])
def test_batch_is_exact_original_view_with_same_basis_and_corrupt_result_rejection(
    monkeypatch, status, saved_kind, error,
):
    fake_saved_parser(monkeypatch)
    original = before_module()
    r = run(status=status, artifact="artifact-a" if status in {"completed", "degraded"} else None,
            error=error)
    data = {
        ResearchTraceRecord: [trace(r.run_id, 2), trace(r.run_id, 1)],
        ObservationRecord: [ObservationRecord(event_id=r.event_id, received_at=AT + timedelta(seconds=2),
                                              text="Later"),
                            ObservationRecord(event_id=r.event_id, received_at=AT, text="Original title")],
        ArtifactRecord: [ArtifactRecord(artifact_id="artifact-a", gate_status="research_only")],
        DshSessionLinkRecord: [DshSessionLinkRecord(run_id=r.run_id)],
    }
    if saved_kind != "missing":
        data[ResearchResultRecord] = [result(r.run_id, corrupt=saved_kind == "corrupt")]
    old_session, new_session = Session(data), Session(data)
    old_calls, new_calls = [], []
    expected = view_service(original, old_calls)._run_view(old_session, r)
    material = recent._read_recent_run_rows(new_session, [r])
    actual = view_service(current, new_calls)._run_view(new_session, r, _read_rows=material[r.run_id])
    assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
    assert [(kind, key) for kind, _, key in new_calls] == [(kind, key) for kind, _, key in old_calls]
    assert all(session is new_session for _, session, _ in new_calls)
    assert all(kind == "batch" for kind, _ in new_session.calls)


def test_eight_runs_batch_queries_and_fresh_reads_not_cross_request_cache(monkeypatch):
    fake_saved_parser(monkeypatch)
    runs = [run(f"run-{i}") for i in range(8)]
    data = {ResearchTraceRecord: [trace(r.run_id, 1) for r in runs]}
    session = Session(data)
    first = recent._read_recent_run_rows(session, runs)
    assert len(session.calls) == 6  # result, full traces, earliest observation, evidence, facts, link
    data[ResearchTraceRecord].append(trace("run-0", 2))
    second = recent._read_recent_run_rows(session, runs)
    assert first["run-0"].traces[-1].sequence_no == 1
    assert second["run-0"].traces[-1].sequence_no == 2
    assert len(session.calls) == 12
    assert recent._read_recent_run_rows(session, []) == {}
    with pytest.raises(ValueError, match="research_recent_read_scope_invalid"):
        recent._read_recent_run_rows(session, runs + [run("ninth")])


def test_run_inputs_cannot_be_used_for_another_run():
    material = current._RunViewReadRows("wrong", current._SavedResearchResult(None), [], None,
                                        None, [], [], None)
    with pytest.raises(ValueError, match="research_run_view_read_identity_mismatch"):
        object.__new__(current.ResearchQueryService)._run_view(None, run(), _read_rows=material)


def test_invalid_saved_schema_payload_and_read_failures_are_not_success():
    wrong = result("run-a")
    wrong.schema_version = "unknown"
    assert current._validated_saved_research_result(wrong, "run-a").issue_code == "saved_research_result_schema_invalid"
    assert current._validated_saved_research_result(result("other"), "run-a").result is None
    invalid = result("run-a", payload='{}')
    assert current._validated_saved_research_result(invalid, "run-a").issue_code == "saved_research_result_payload_invalid"
    error = PermissionError()
    class Broken:
        def scalars(self, statement): raise error
    with pytest.raises(PermissionError) as caught:
        recent._read_recent_run_rows(Broken(), [run()])
    assert caught.value is error


def functions(path):
    return {n.name: n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, ast.FunctionDef)}


def approved_projection_text(text):
    child_before = ('                RunRecord.parent_run_id == run_id,\n'
                    '                RunRecord.available_at.is_not(None),\n')
    child_after = ('                RunRecord.parent_run_id == run_id,\n'
                   '                RunRecord.admission_origin == "scheduled_recheck",\n'
                   '                RunRecord.available_at.is_not(None),\n')
    review_before = ('    review_times = [item.next_review_at for item in horizons]\n'
                     '    if scheduled_recheck_at is not None:\n'
                     '        review_times.append(scheduled_recheck_at)\n')
    review_after = ('    # Report suggestions come from the saved report. A child\'s admission time\n'
                    '    # is execution history, including manual commands; durable review plans\n'
                    '    # supply their own trigger_at through the separate review-plans projection.\n'
                    '    # Keep the scheduled_recheck_at argument for existing projection callers.\n'
                    '    review_times = [item.next_review_at for item in horizons]\n')
    assert text.count(child_before) == text.count(review_before) == 1
    return text.replace(child_before, child_after).replace(review_before, review_after)


def test_result_schema_hash_parser_and_basis_guards_are_original_ast():
    old = functions(BEFORE / "packages/query_views/research/service.py")
    new = functions(ROOT / "packages/query_views/research/service.py")
    assert [ast.dump(n) for n in old["_saved_research_result"].body[2:]] == [
        ast.dump(n) for n in new["_validated_saved_research_result"].body[1:]]
    for name in ("_coverage_basis_for_run", "_submission_basis_for_run", "_fallback_coverage",
                 "_fallback_stop_reason", "_failure_view", "_trace_view", "_evidence_view", "_fact_view"):
        assert ast.dump(new[name]) == ast.dump(old[name])
    changed = {"_run_view", "_saved_research_result", "_observed_runtime_id"}
    projection = {n.name: n for n in ast.walk(ast.parse(approved_projection_text(
        (BEFORE / "packages/query_views/research/service.py").read_text(encoding="utf-8"))))
        if isinstance(n, ast.FunctionDef)}
    for name in old.keys() - changed:
        expected = projection[name] if name in {"_detail_in_session", "_action_summary"} else old[name]
        assert ast.dump(new[name]) == ast.dump(expected)


def test_marker_projection_only_changes_columns_and_row_execution():
    old = functions(BEFORE / "packages/query_views/market_risk.py")["_markers"]
    new = functions(ROOT / "packages/query_views/market_risk.py")["_markers"]
    class Normalize(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id == "select":
                first = node.args[0]
                node.args = [first.value if isinstance(first, ast.Attribute) else first]
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "session" and node.func.attr == "execute":
                node.func.attr = "scalars"
            return node
    assert ast.dump(old) == ast.dump(Normalize().visit(new))


def test_marker_columns_truncation_and_known_time_receipts_are_preserved():
    report = [SimpleNamespace(artifact_id=f"a{i}", run_id="run", headline="headline", created_at=AT)
              for i in range(51)]
    interrupted = [SimpleNamespace(run_id="cancelled", status="cancelled", finished_at=AT)]
    facts = [SimpleNamespace(fact_version_id="fact", known_at=AT, observed_at=AT-timedelta(hours=1))]
    events = [SimpleNamespace(macro_event_id="macro", created_at=AT, scheduled_at=AT+timedelta(days=1))]
    expected_columns = [("artifact_id", "run_id", "headline", "created_at"),
                        ("run_id", "status", "finished_at"),
                        ("fact_version_id", "known_at", "observed_at"),
                        ("macro_event_id", "created_at", "scheduled_at")]
    queries = []
    class MarkerSession:
        def execute(self, statement):
            assert tuple(c.key for c in statement.selected_columns) == expected_columns[len(queries)]
            statement.compile(dialect=sqlite.dialect())
            queries.append(statement)
            return Rows((report, interrupted, facts, events)[len(queries)-1])
    @contextmanager
    def scope(): yield MarkerSession()
    query = object.__new__(MarketRiskQuery)
    query.database = SimpleNamespace(session=scope)
    markers, truncated = query._markers(SimpleNamespace(asset_class="crypto", instrument="BTC-USDT"),
                                        start=AT-timedelta(minutes=30), end=AT)
    assert truncated and len(markers) == 53
    assert [(m["at"], m["marker_id"]) for m in markers] == sorted((m["at"], m["marker_id"]) for m in markers)
    assert next(m for m in markers if m["marker_id"] == "fact")["occurred_at"] == AT-timedelta(hours=1)
    assert next(m for m in markers if m["marker_id"] == "macro")["occurred_at"] == AT+timedelta(days=1)
    assert all(m["source_ref"] == m["marker_id"] for m in markers)
