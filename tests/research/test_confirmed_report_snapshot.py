"""Pure permission/transport controls; no SQLite, HTTP, provider, or product run.

The existing actual39 quality bytes are a report transport fixture only. Fake
read sessions do not establish a new Saved/ACK or a fresh producer qualification.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from apps.hub_api import research_quality_routes as routes
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import (
    BundleArchive, PublicationBundleLifecycle, encoded, sha,
)
from packages.kernel.decision_hub_kernel.application.research_publication_jobs48 import (
    DurablePublicationLifecycle, NATIVE_FIELDS,
)
from packages.kernel.decision_hub_kernel.application.research_quality_view import ResearchQualityViewV1
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleConfirmationRecord as Ack,
    PublicationBundleJobRecord as Job,
    PublicationBundleRecord as Saved,
)
from packages.kernel.decision_hub_kernel.ports.research_publication_bundle import FrozenPublicationBundleV1
from packages.kernel.decision_hub_kernel.ports.research_report_snapshot import ConfirmedReportSnapshotV1

DEV = Path(__file__).resolve().parents[2]
ROOT = DEV.parent / "decision-hub"


class ReadSession:
    def __init__(self, records, publication, selection):
        self.records, self.publication, self.selection = records, publication, selection

    def get(self, kind, key, **_options):
        return self.records.get((kind, key))

    def scalars(self, _query):
        return [self.publication]

    def connection(self):
        return self

    def exec_driver_sql(self, query, parameters):
        assert query.startswith("SELECT output_id,run_id,role,schema_version,payload_json,")
        assert parameters == (self.publication.run_id, "graph_recipe_selection")
        return SimpleNamespace(fetchall=lambda: [tuple(self.selection[name] for name in NATIVE_FIELDS)])


@pytest.fixture
def snapshot(tmp_path):
    raw = (ROOT / "artifacts/market-risk-implementation-20261007/B2/release39/"
                  "eth-terminal39_01/quality.json").read_bytes()
    assert sha(raw) == "49e3b32d49357f023879317c68ec40d98b277e8e39c6bfbe0a3dcf084e3368c1"
    quality = ResearchQualityViewV1.model_validate_json(raw)
    assert quality.decision is not None
    bundle = FrozenPublicationBundleV1(
        run_id=quality.run_id, event_id="snapshot-fixture-event", artifact_id="snapshot-fixture-artifact",
        publication_ref=quality.publication_ref, recipe_selection_ref=quality.recipe_selection_ref,
        original_published_at=quality.published_at, original_read_at=quality.as_of,
        quality_json=quality.model_dump_json(), report_sha256=quality.decision.full_report_hash,
        report_bytes=len(quality.decision.full_report_json.encode()),
        material_ref="materials/" + "b" * 64 + ".json", material_sha256="b" * 64,
        material_bytes=1, trusted_reader_source_sha256="c" * 64,
    )
    archive = BundleArchive(tmp_path / "archive", max_bundle_bytes=16 * 1024 * 1024,
                            max_material_bytes=192 * 1024 * 1024)
    reference, digest, size = archive.put(bundle)
    selection_body = encoded({"schema_version": "research-graph-recipe-selection.v3"}).decode()
    selection = dict(zip(NATIVE_FIELDS, (
        "snapshot-fixture-selection", bundle.run_id, "graph_recipe_selection",
        "research-graph-recipe-selection.v3", selection_body, sha(selection_body.encode()),
        quality.as_of.isoformat(),
    )))
    selection_json = encoded(selection).decode()
    body = {"schema_version": bundle.publication_ref.schema_version,
            "run_id": bundle.run_id, "event_id": bundle.event_id, "artifact_id": bundle.artifact_id,
            "quality_binding": {"source_result_hash": bundle.report_sha256,
                                "quality_decision_hash": quality.decision.decision_hash,
                                "quality_sidecar_id": quality.quality_sidecar_id},
            "graph_final_ref": {"row": quality.graph_final_ref.model_dump(mode="json")},
            "gate": {"status": quality.gate_status}}
    payload = encoded(body).decode()
    publication = RunRoleOutputRecord(output_id=bundle.publication_ref.output_id,
        run_id=bundle.run_id, role="research_publication_source", schema_version=body["schema_version"],
        payload_json=payload, payload_hash=sha(payload.encode()), created_at=quality.published_at)
    # The source hash must bind the fixture bundle reference, too. Rebuild the
    # fixture reference; this is test data, never a forged product publication.
    quality_data = quality.model_dump(mode="python")
    quality_data["publication_ref"]["payload_hash"] = publication.payload_hash
    quality = ResearchQualityViewV1.model_validate(quality_data)
    bundle = FrozenPublicationBundleV1.model_validate({**bundle.model_dump(mode="python"),
        "publication_ref": quality.publication_ref, "quality_json": quality.model_dump_json()})
    reference, digest, size = archive.put(bundle)
    job = Job(run_id=bundle.run_id, request_id="snapshot-fixture-request", event_id=bundle.event_id,
        artifact_id=bundle.artifact_id, publication_id=publication.output_id,
        publication_hash=publication.payload_hash, publication_schema=publication.schema_version,
        publication_created_at=quality.published_at, generation=3, owner=None, lease_expires_at=None,
        state="confirmed", bundle_hash=digest, confirmed_at=quality.as_of, updated_at=quality.as_of,
        state_version=4, selection_native_json=selection_json,
        selection_native_hash=sha(selection_json.encode()), reader_version="frozen-publication-reader.v1")
    saved = Saved(bundle_hash=digest, run_id=bundle.run_id, request_id=job.request_id, generation=3,
        bundle_ref=reference, bundle_bytes=size, material_ref=bundle.material_ref,
        material_hash=bundle.material_sha256, material_bytes=bundle.material_bytes,
        report_hash=bundle.report_sha256, report_bytes=bundle.report_bytes,
        reader_hash=bundle.trusted_reader_source_sha256, prepared_at=quality.as_of)
    ack = Ack(run_id=bundle.run_id, request_id=job.request_id, generation=3,
        bundle_hash=digest, confirmed_at=quality.as_of)
    run = RunRecord(run_id=bundle.run_id, event_id=bundle.event_id, artifact_id=bundle.artifact_id,
                    status="completed", finished_at=quality.published_at)
    records = {(RunRecord, bundle.run_id): run, (Job, bundle.run_id): job,
               (Saved, digest): saved, (Ack, bundle.run_id): ack}
    life = object.__new__(DurablePublicationLifecycle)
    life.archive = archive
    life.database = None
    life.clock = lambda: quality.as_of
    reads = []
    hooks = []

    @contextmanager
    def ro():
        if hooks:
            hooks.pop(0)()
        reads.append(len(reads))
        yield ReadSession(records, publication, selection)

    life._ro = ro
    return SimpleNamespace(life=life, archive=archive, bundle=bundle, saved=saved, job=job,
        ack=ack, quality=quality, publication=publication, records=records, reads=reads, hooks=hooks)


def endpoints(snapshot):
    router = routes.research_quality_router(None, publication_lifecycle=snapshot.life,
                                            clock=snapshot.life.clock)
    return {route.path: route.endpoint for route in router.routes}


def test_snapshot_is_distinct_and_original_bytes_match(snapshot):
    paths = endpoints(snapshot)
    response = Response()
    value = paths["/v1/research/runs/{run_id}/report/snapshot"](snapshot.bundle.run_id, response)
    assert isinstance(value, ConfirmedReportSnapshotV1)
    assert value.read_scope == "confirmed_report_snapshot"
    assert value.material_audit == "not_checked_this_read"
    assert value.source_bundle_sha256 == snapshot.saved.bundle_hash
    download = paths["/v1/research/runs/{run_id}/report/snapshot/download"](snapshot.bundle.run_id)
    assert download.body == snapshot.bundle.report()
    assert hashlib.sha256(download.body).hexdigest() == value.report_sha256
    assert download.headers["ETag"] == '"' + value.report_sha256 + '"'
    assert response.headers["X-Decision-Hub-Report-Read-Mode"] == value.read_scope
    assert response.headers["X-Decision-Hub-Report-Material-Audit"] == value.material_audit
    assert len(snapshot.reads) == 4


@pytest.mark.parametrize("state", ["queued", "prepared", "unconfirmed", "revoked", "cancelled"])
def test_unconfirmed_and_revoked_never_fallback(snapshot, state, monkeypatch):
    snapshot.job.state = state
    monkeypatch.setattr(routes.ResearchQualityQuery, "read", lambda *_a, **_k: pytest.fail("fallback"))
    with pytest.raises(HTTPException) as error:
        endpoints(snapshot)["/v1/research/runs/{run_id}/report/snapshot"](snapshot.bundle.run_id, Response())
    assert (error.value.status_code, error.value.detail) == (
        409, "research_report_snapshot_verification_failed")


@pytest.mark.parametrize("change", ["no_ack", "ack_generation", "fence", "source", "selection", "index"])
def test_permission_and_binding_fail_closed(snapshot, change):
    if change == "no_ack":
        snapshot.records.pop((Ack, snapshot.bundle.run_id))
    elif change == "ack_generation":
        snapshot.ack.generation += 1
    elif change == "fence":
        snapshot.archive.exclusive(snapshot.archive.fence_ref(snapshot.bundle.run_id), b"{}")
    elif change == "source":
        snapshot.publication.payload_json += " "
    elif change == "selection":
        snapshot.job.selection_native_hash = "d" * 64
    else:
        snapshot.saved.report_hash = "d" * 64
    with pytest.raises((ValueError, PermissionError)):
        snapshot.life.read_confirmed_report_snapshot(snapshot.bundle.run_id)


def test_changed_exit_version_blocks_serving(snapshot):
    snapshot.hooks.extend([lambda: None, lambda: setattr(snapshot.job, "state_version", 5)])
    with pytest.raises(ValueError, match="report_snapshot_serving_head_changed"):
        snapshot.life.read_confirmed_report_snapshot(snapshot.bundle.run_id)


def test_bundle_bytes_tampering_blocks_both_modes(snapshot):
    path = snapshot.archive._path(snapshot.saved.bundle_ref)
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + (b" " if raw[-1:] != b" " else b"x"))
    with pytest.raises(ValueError, match="publication_bundle_saved_bytes_changed"):
        snapshot.life.read_confirmed_report_snapshot(snapshot.bundle.run_id)
    with pytest.raises(ValueError, match="publication_bundle_saved_bytes_changed"):
        snapshot.life.read_with_head(snapshot.bundle.run_id)


def test_material_damage_is_not_a_fresh_audit_and_legacy_still_rejects(snapshot):
    material = snapshot.archive._path(snapshot.bundle.material_ref)
    material.parent.mkdir(parents=True, exist_ok=True)
    material.write_bytes(b"x")  # Declared material hash is different.
    value, head = snapshot.life.read_confirmed_report_snapshot(snapshot.bundle.run_id)
    assert value == snapshot.bundle and head["material_audit"] == "not_checked_this_read"
    with pytest.raises(ValueError):
        snapshot.life.read_with_head(snapshot.bundle.run_id)
    with pytest.raises(HTTPException) as error:
        endpoints(snapshot)["/v1/research/runs/{run_id}/quality"](snapshot.bundle.run_id, Response())
    assert error.value.status_code == 409


def test_legacy_has_no_snapshot_and_get_does_not_create_job(snapshot):
    snapshot.records.pop((Job, snapshot.bundle.run_id))
    with pytest.raises(HTTPException) as error:
        endpoints(snapshot)["/v1/research/runs/{run_id}/report/snapshot"](snapshot.bundle.run_id, Response())
    assert (error.value.status_code, error.value.detail) == (404, "research_report_snapshot_not_available")
    assert (Job, snapshot.bundle.run_id) not in snapshot.records


def test_legacy_unknown_commit_fence_never_grants_snapshot(snapshot):
    snapshot.records.pop((Job, snapshot.bundle.run_id))
    snapshot.archive.exclusive(snapshot.archive.fence_ref(snapshot.bundle.run_id), b"{}")
    with pytest.raises(HTTPException) as error:
        endpoints(snapshot)["/v1/research/runs/{run_id}/report/snapshot"](snapshot.bundle.run_id, Response())
    assert (error.value.status_code, error.value.detail) == (
        409, "research_report_snapshot_verification_failed")


def test_snapshot_transport_rejects_replaced_report_bytes(snapshot):
    endpoint = endpoints(snapshot)["/v1/research/runs/{run_id}/report/snapshot"]
    value = endpoint(snapshot.bundle.run_id, Response())
    body = copy.deepcopy(value.model_dump(mode="python"))
    quality = json.loads(body["quality_json"])
    quality["decision"]["full_report_json"] += " "
    body["quality_json"] = json.dumps(quality)
    with pytest.raises(ValueError, match="report_snapshot_report_bytes_changed"):
        ConfirmedReportSnapshotV1.model_validate(body)


_ORIGINAL_DIAGNOSTIC_LOG = (
    '                    logging.getLogger(__name__).warning("publication_failure_diagnostic_unavailable type=%s claim=%s",\n'
    '                        type(diagnostic_error).__name__,sha(encoded([claim.run_id,claim.request_id,claim.generation])))'
)
_AUTHORIZED_DIAGNOSTIC_LOG = (
    '                    logging.getLogger(__name__).warning(\n'
    '                        "publication_failure_diagnostic_unavailable type=%s producer_type=%s claim=%s",\n'
    '                        _publication_failure_class(diagnostic_error), _publication_failure_class(error),\n'
    '                        sha(encoded([claim.run_id,claim.request_id,claim.generation])))'
)

_JOBS_RELATIVE = "packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py"
_JOBS_CHAIN = (
    ("diagnostic-gap-recovery01", "b21cb4dadb286724af6e49669013552d670c3d51e199673f366f84536a47a1c2",
     "208950450c2d72f60fadc8977385c1ec23c032b3fd81f2a4d5d839737ed49233",
     "4e340cfc701509b3e1c23e9bc87c5238bcc4087f2cb0076c643c14a7d31381ef"),
    ("execution-control-jobs01", "b4a1f6113f523d91a8ad13ab1af66cabfbf716395bae12b0b0f28c22dcdd6085",
     "4e340cfc701509b3e1c23e9bc87c5238bcc4087f2cb0076c643c14a7d31381ef",
     "e08bea7a367d3d56d1f11b1efb750bec2d21eb75783bb38b877daac147dcc64a"),
    ("execution-control-jobs02", "4d44e48bbbce3d0056d45e25a838b574a0bcf8baf63e82311160d847bc7eb3de",
     "e08bea7a367d3d56d1f11b1efb750bec2d21eb75783bb38b877daac147dcc64a",
     "a77864ccd60b243b41e0ef0435ce4de07fde18bd0cd46a367da31239034c87e9"),
    ("known-cancel-recovery01", "b3e61106a2d518bcf73f2cf7b18c0ba936a1375949c28b003e519804225e5bb4",
     "a77864ccd60b243b41e0ef0435ce4de07fde18bd0cd46a367da31239034c87e9",
     "a2fdbffb1df6a274795704b9461924f4ecf98ab35fb4c477e15cea24c5ebacdb"),
)


def _source_functions(source):
    result = {}
    def visit(node, parents=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parents = (*parents, node.name)
            if not isinstance(node, ast.ClassDef):
                result[parents] = ast.get_source_segment(source, node)
        for child in ast.iter_child_nodes(node):
            visit(child, parents)
    visit(ast.parse(source))
    return result


def _trusted_jobs_chain():
    chain = []
    previous = None
    for directory, seal_hash, before_hash, after_hash in _JOBS_CHAIN:
        base = DEV / "artifacts/core-goal-20261011" / directory
        seal = base / "candidate.json"
        assert sha(seal.read_bytes()) == seal_hash
        document = json.loads(seal.read_bytes())
        known_cancel = directory == "known-cancel-recovery01"
        metadata = document["production_files" if known_cancel else "files"][0]
        assert metadata["path"] == _JOBS_RELATIVE
        assert metadata["before_sha256"] == before_hash
        assert metadata["sha256" if known_cancel else "candidate_sha256"] == after_hash
        before = base / ("root_before" if known_cancel else "before") / _JOBS_RELATIVE
        after = base / "candidate" / _JOBS_RELATIVE
        assert sha(before.read_bytes()) == before_hash
        assert sha(after.read_bytes()) == after_hash
        if previous is not None:
            assert previous == before.read_bytes()
        chain.append((_source_functions(before.read_text(encoding="utf-8")),
                      _source_functions(after.read_text(encoding="utf-8"))))
        previous = after.read_bytes()
    # Pins new functions/constants too. This is not a function-name exemption.
    assert (DEV / _JOBS_RELATIVE).read_bytes() == previous
    return chain


def _assert_original_function(name, original, actual):
    worker_name = ("PublicationBundleWorker", "process_one")
    retry_names = {
        ("DurablePublicationLifecycle", "_retry_archive_proof49"),
        ("DurablePublicationLifecycle", "retry_unconfirmed"),
        ("DurablePublicationLifecycle", "retry_unconfirmed", "write"),
    }
    if name == worker_name:
        seal = DEV / "artifacts/core-goal-20261011/failure-diagnostics01/candidate.json"
        assert sha(seal.read_bytes()) == "19a793d9c146fd0b9c0c16ce8d170e6f68925d177fa38b2754134b91b2c905da"
        assert original.count(_ORIGINAL_DIAGNOSTIC_LOG) == 1
        original = original.replace(_ORIGINAL_DIAGNOSTIC_LOG, _AUTHORIZED_DIAGNOSTIC_LOG, 1)
        gap, execution, stop, known_cancel = _trusted_jobs_chain()
        assert original == gap[0][name] == gap[1][name] == execution[0][name]
        assert execution[1][name] == stop[0][name]
        assert stop[1][name] == known_cancel[0][name]
        original = known_cancel[1][name]
    elif name in retry_names:
        gap, execution, stop, known_cancel = _trusted_jobs_chain()
        assert original == gap[0][name]
        assert gap[1][name] == execution[0][name] == execution[1][name] == stop[0][name] == stop[1][name]
        assert stop[1][name] == known_cancel[0][name] == known_cancel[1][name]
        original = gap[1][name]
    elif name in {("DurablePublicationLifecycle", "cancel_preparation"),
                  ("DurablePublicationLifecycle", "cancel_preparation", "write")}:
        gap, execution, stop, known_cancel = _trusted_jobs_chain()
        assert original == gap[0][name] == gap[1][name] == execution[0][name] == execution[1][name]
        assert original == stop[0][name] == stop[1][name] == known_cancel[0][name]
        original = known_cancel[1][name]
    assert actual == original, name


@pytest.mark.parametrize("mutation", ["claim_one", "save_verified", "fenced", "fail_claim"])
def test_exact_log_delta_does_not_exempt_worker_permission_changes(mutation):
    original = 'def process_one(self):\n' + _ORIGINAL_DIAGNOSTIC_LOG + "\n"
    original += '    self.lifecycle.claim_one(); self.lifecycle.save_verified(); '
    original += 'self.lifecycle.archive.fenced(); self.lifecycle.fail_claim()'
    authorized = original.replace(_ORIGINAL_DIAGNOSTIC_LOG, _AUTHORIZED_DIAGNOSTIC_LOG, 1)
    changed = authorized.replace(mutation, mutation + "_changed", 1)
    with pytest.raises(AssertionError):
        _assert_original_function(("PublicationBundleWorker", "process_one"), original, changed)


@pytest.mark.parametrize("name, mutation", [
    (("PublicationBundleWorker", "process_one"), "claim_one"),
    (("PublicationBundleWorker", "process_one"), "save_verified"),
    (("PublicationBundleWorker", "process_one"), "fenced"),
    (("PublicationBundleWorker", "process_one"), "fail_claim"),
    (("PublicationBundleWorker", "process_one"), "cancel_preparation"),
    (("DurablePublicationLifecycle", "retry_unconfirmed", "write"), "bundle_hash"),
    (("DurablePublicationLifecycle", "cancel_preparation", "write"), "owner"),
    (("DurablePublicationLifecycle", "cancel_preparation", "write"), "generation"),
])
def test_sealed_composite_deltas_do_not_exempt_permissions(name, mutation):
    original = _source_functions((DEV / "artifacts/report-snapshot-20261011/before" / _JOBS_RELATIVE)
                                 .read_text(encoding="utf-8"))[name]
    actual = _source_functions((DEV / _JOBS_RELATIVE).read_text(encoding="utf-8"))[name]
    assert mutation in actual
    with pytest.raises(AssertionError):
        _assert_original_function(name, original, actual.replace(mutation, mutation + "_changed", 1))


@pytest.mark.parametrize("relative", [
    "packages/kernel/decision_hub_kernel/application/research_publication_bundle_lifecycle.py",
    "packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py",
    "apps/hub_api/research_quality_routes.py",
])
def test_existing_functions_are_unchanged_from_root55(relative):
    before = (DEV / "artifacts/report-snapshot-20261011/before" / relative).read_text(encoding="utf-8")
    after = (DEV / relative).read_text(encoding="utf-8")
    def functions(source):
        found = {}
        def visit(node, parents=()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parents = (*parents, node.name)
                if not isinstance(node, ast.ClassDef):
                    found[parents] = ast.get_source_segment(source, node)
            for child in ast.iter_child_nodes(node):
                visit(child, parents)
        visit(ast.parse(source))
        return found
    before_functions, after_functions = functions(before), functions(after)
    for name, original in before_functions.items():
        if name == ("research_quality_router",):
            continue  # Adds endpoints inside this factory; old endpoints stay exact.
        _assert_original_function(name, original, after_functions[name])
