import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import (
    ROLE,
    ModelDispatchService,
)
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from tests.dsh_native.test_dsh_session_links import database, identity, reserved


def setup_binding(tmp_path, *, method=True):
    db = database(tmp_path / "model.sqlite3")
    _, run_id, submit = reserved(db)
    if method:
        ResearchMethodService(db).freeze(
            run_id,
            {"upstream_identity": identity().model_dump(mode="json")},
            request_hash=submit.request_hash,
        )
    payload = DshModelDispatchBinding(
        schema_version="dsh-model-dispatch-binding.v1",
        run_id=run_id,
        dsh_session_id=submit.deterministic_session_id,
        request_hash=submit.request_hash,
        generation=1,
        plugin_build_hash=identity().plugin_build_hash,
        purpose="conversation",
        provider="live-route",
        model="research-model",
        reasoning_effort="high",
        temperature=None,
        max_tokens=8192,
        stop_sequences_hash=None,
    )
    return db, payload


def test_binding_survives_process_reconstruction_without_rewriting_receipt(tmp_path):
    db, payload = setup_binding(tmp_path)
    ModelDispatchService(db).bind(payload)
    assert ModelDispatchService(db).bind(payload) == payload
    reopened = Database(str(db.engine.url))
    assert ModelDispatchService(reopened).get(payload.run_id) == payload
    reopened.engine.dispose()
    with db.session() as s:
        assert s.query(RunRoleOutputRecord).filter_by(role=ROLE).count() == 1


def test_next_authorized_generation_keeps_first_model_binding(tmp_path):
    db, payload = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    service.bind(payload)
    with db.session() as s:
        s.get(DshSessionLinkRecord, payload.run_id).generation = 2
    next_round = payload.model_copy(update={"generation": 2})
    assert service.bind(next_round) == next_round
    assert service.get(payload.run_id) == payload
    with pytest.raises(ValueError, match="dsh_model_configuration_changed_during_run"):
        service.bind(next_round.model_copy(update={"model": "new-round-model"}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "other-route"),
        ("model", "other-model"),
        ("reasoning_effort", "low"),
        ("temperature", 0.5),
        ("max_tokens", 4096),
        ("stop_sequences_hash", "f" * 64),
    ],
)
def test_changed_model_controls_rejected_before_dispatch(tmp_path, field, value):
    db, payload = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    service.bind(payload)
    with pytest.raises(ValueError, match="dsh_model_configuration_changed_during_run"):
        service.bind(payload.model_copy(update={field: value}))
    assert service.get(payload.run_id) == payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("dsh_session_id", "other-session"),
        ("generation", 2),
        ("request_hash", "f" * 64),
        ("plugin_build_hash", "f" * 64),
        ("purpose", "compaction"),
    ],
)
def test_wrong_submission_identity_cannot_create_binding(tmp_path, field, value):
    db, payload = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    with pytest.raises(ValueError, match="dsh_model_dispatch_identity_mismatch"):
        service.bind(payload.model_copy(update={field: value}))
    assert service.get(payload.run_id) is None


def test_missing_method_and_completed_run_do_not_gain_new_receipts(tmp_path):
    db, payload = setup_binding(tmp_path, method=False)
    service = ModelDispatchService(db)
    with pytest.raises(ValueError, match="dsh_model_dispatch_method_missing"):
        service.bind(payload)
    with db.session() as s:
        s.get(RunRecord, payload.run_id).status = "completed"
    with pytest.raises(ValueError, match="dsh_model_dispatch_run_terminal"):
        service.bind(payload)
    assert service.get(payload.run_id) is None


def test_corrupted_binding_is_not_recreated(tmp_path):
    db, payload = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    service.bind(payload)
    with db.session() as s:
        row = s.query(RunRoleOutputRecord).filter_by(role=ROLE).one()
        row.payload_json = row.payload_json.replace("research-model", "changed-model")
    with pytest.raises(ValueError, match="dsh_model_binding_corrupt"):
        service.bind(payload)


def test_callback_route_requires_auth_and_confirms_saved_parameters(tmp_path):
    db, payload = setup_binding(tmp_path)
    app = create_app(db, source_connectors=[], dsh_callback_secret="bridge-test-only")
    client = TestClient(app)
    url = f"/v1/dsh/sessions/{payload.run_id}/model-binding"
    headers = {"X-Decision-Hub-Bridge-Key": "bridge-test-only"}
    assert client.put(url, json=payload.model_dump()).status_code == 401
    assert client.get(url, headers=headers).status_code == 404
    saved = client.put(url, json=payload.model_dump(), headers=headers)
    assert saved.status_code == 200
    assert client.get(url, headers=headers).json() == payload.model_dump()
    changed = payload.model_copy(update={"model": "changed"})
    rejected = client.put(url, json=changed.model_dump(), headers=headers)
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "dsh_model_configuration_changed_during_run"


def test_readiness_uses_the_matching_run_and_rejects_corrupted_binding(tmp_path):
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    from packages.query_views.evolution_readiness import evolution_readiness

    db, payload = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    service.bind(payload)
    view = evolution_readiness(db, utcnow()).research_method
    assert view["model_dispatch"]["status"] == "verified"
    assert view["model_dispatch"]["binding"]["run_id"] == payload.run_id
    assert not view["deployment_connected"]
    with db.session() as s:
        row = s.query(RunRoleOutputRecord).filter_by(role=ROLE).one()
        row.payload_hash = "0" * 64
    assert evolution_readiness(db, utcnow()).research_method["model_dispatch"] == {
        "status": "invalid",
        "binding": None,
    }
    second, _ = RunService(db).create("second-event")
    ResearchMethodService(db).freeze(
        second,
        {"upstream_identity": identity().model_dump(mode="json")},
        request_hash="d" * 64,
    )
    assert evolution_readiness(db, utcnow()).research_method["model_dispatch"] == {
        "status": "not_recorded",
        "binding": None,
    }


@pytest.mark.parametrize("mode", ["matched", "changed-model", "missing-header"])
def test_archive_audit_rejects_missing_or_changed_model_evidence(tmp_path, mode):
    import json

    import zstandard

    from tools.ops.audit_model_dispatch import audit

    db, payload = setup_binding(tmp_path)
    ModelDispatchService(db).bind(payload)
    root = tmp_path / "sessions"
    archive = root / "workspace" / payload.dsh_session_id / "session.jsonl.zstd"
    archive.parent.mkdir(parents=True)
    rows = [{"type": "session", "id": payload.dsh_session_id, "createdAt": 1700000000000}]
    if mode != "missing-header":
        rows.append(
            {
                "type": "request/header",
                "seq": 1,
                "time": 1700000000001,
                "data": {
                    "header": {
                        "system": "private prompt text",
                        "config": {
                            "provider": payload.provider,
                            "model": payload.model if mode == "matched" else "other-model",
                            "reasoningEffort": "high",
                            "maxTokens": 8192,
                        },
                    }
                },
            }
        )
    archive.write_bytes(zstandard.compress("\n".join(json.dumps(r) for r in rows).encode()))
    result = audit(db, root, payload.run_id)
    assert all(result["checks"].values()) == (mode == "matched")
    assert not result["provider_configuration_attested"]
    assert "private prompt text" not in json.dumps(result)


@pytest.mark.parametrize("parent", ["root", "child"])
def test_child_binding_freezes_each_session_and_requires_existing_parent(tmp_path, parent):
    db, root = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    child = root.model_copy(
        update={
            "execution_session_id": "child-one",
            "parent_session_id": root.dsh_session_id,
            "model": "child-model",
        }
    )
    with pytest.raises(ValueError, match="lineage_invalid"):
        service.bind(child)
    service.bind(root)
    service.bind(child)
    target = (
        child
        if parent == "root"
        else child.model_copy(
            update={
                "execution_session_id": "grandchild",
                "parent_session_id": "child-one",
                "model": "review-model",
            }
        )
    )
    service.bind(target)
    assert service.get(root.run_id, target.execution_session_id) == target
    assert service.get(root.run_id) == root
    with pytest.raises(ValueError, match="configuration_changed"):
        service.bind(target.model_copy(update={"model": "changed"}))
    reopened = Database(str(db.engine.url))
    assert ModelDispatchService(reopened).get(root.run_id, target.execution_session_id) == target
    reopened.engine.dispose()


@pytest.mark.parametrize(
    "execution,parent",
    [
        (None, "parent"),
        ("child", None),
        ("child", "child"),
        ("child", "unknown"),
        ("root", "root"),
    ],
)
def test_invalid_child_lineage_does_not_create_receipts(tmp_path, execution, parent):
    db, root = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    service.bind(root)
    value = root.model_copy(
        update={
            "execution_session_id": root.dsh_session_id if execution == "root" else execution,
            "parent_session_id": root.dsh_session_id if parent == "root" else parent,
        }
    )
    with pytest.raises(ValueError, match="lineage_invalid"):
        service.bind(value)
    assert service.children(root.run_id) == []


@pytest.mark.parametrize(
    "mode", ["matched", "drift", "missing-binding", "missing-archive", "wrong-parent"]
)
def test_descendant_audit_covers_children_and_ignores_inherited_parent_requests(tmp_path, mode):
    import json

    import zstandard

    from tools.ops.audit_model_dispatch import audit

    db, root = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    service.bind(root)
    child = root.model_copy(
        update={
            "execution_session_id": "child-one",
            "parent_session_id": root.dsh_session_id,
            "model": "child-model",
        }
    )
    if mode != "missing-binding":
        service.bind(child)
    sessions = tmp_path / "sessions"
    for sid, model, parent, born in [
        (root.dsh_session_id, root.model, None, 1000),
        (
            "child-one",
            "wrong" if mode == "drift" else "child-model",
            "unknown" if mode == "wrong-parent" else root.dsh_session_id,
            2000,
        ),
    ]:
        if sid == "child-one" and mode == "missing-archive":
            continue
        path = (
            sessions
            / ("root-group" if parent is None else "other-group")
            / sid
            / "session.jsonl.zstd"
        )
        path.parent.mkdir(parents=True)
        config = {
            "provider": root.provider,
            "model": model,
            "reasoningEffort": "high",
            "maxTokens": 8192,
        }
        rows = [{"type": "session", "id": sid, "parentSession": parent, "createdAt": born}]
        if parent:
            rows.append(
                {
                    "type": "request/header",
                    "seq": 1,
                    "time": 1001,
                    "data": {"header": {"config": {**config, "model": "inherited"}}},
                }
            )
        rows.append(
            {
                "type": "request/header",
                "seq": 2,
                "time": born + 1,
                "data": {"header": {"config": config}},
            }
        )
        path.write_bytes(zstandard.compress("\n".join(json.dumps(r) for r in rows).encode()))
    result = audit(db, sessions, root.run_id, include_descendants=True)
    assert all(result["checks"].values()) == (mode == "matched")
    if mode == "matched":
        assert result["descendants"][0]["request_header_count"] == 1
    assert not result["deployment_connected"]


def test_child_readiness_and_authenticated_lookup_use_the_same_run(tmp_path):
    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    from packages.query_views.evolution_readiness import evolution_readiness

    db, root = setup_binding(tmp_path)
    service = ModelDispatchService(db)
    service.bind(root)
    child = root.model_copy(
        update={
            "execution_session_id": "child-one",
            "parent_session_id": root.dsh_session_id,
        }
    )
    service.bind(child)
    view = evolution_readiness(db, utcnow()).research_method["child_model_dispatch"]
    assert view == {"status": "recorded", "bindings": [child.model_dump(mode="json")]}
    client = TestClient(create_app(db, source_connectors=[], dsh_callback_secret="test-only"))
    url = f"/v1/dsh/sessions/{root.run_id}/model-binding?execution_session_id=child-one"
    assert client.get(url).status_code == 401
    assert (
        client.get(url, headers={"X-Decision-Hub-Bridge-Key": "test-only"}).json()
        == child.model_dump()
    )
    with db.session() as session:
        row = (
            session.query(RunRoleOutputRecord)
            .filter(RunRoleOutputRecord.role.startswith("research_child_model:"))
            .one()
        )
        row.payload_hash = "0" * 64
    assert evolution_readiness(db, utcnow()).research_method["child_model_dispatch"] == {
        "status": "invalid",
        "bindings": [],
    }
