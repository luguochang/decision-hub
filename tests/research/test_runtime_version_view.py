import json

from packages.contracts_py.decision_hub_contracts import RunStatus
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord, RunRecord
from packages.query_views.research import ResearchQueryService
from tests.dsh_native.test_dsh_web_runtime import FakeHostClient
from tests.evolution.test_research_worker import PACK_ROOT, _admitted_run


async def test_running_version_uses_reserved_identity_and_not_legacy_default(tmp_path):
    db, _, run_id = _admitted_run(tmp_path)
    assert db.get_run_record(run_id).runtime_version == "pending"
    query = ResearchQueryService(db, pack_root=PACK_ROOT)
    assert query.observability_view(run_id).versions.runtime_version == "pending"
    RunService(db).set_status(run_id, RunStatus.running)
    identity = (await FakeHostClient().readiness()).upstream_identity
    with db.session() as session:
        row = session.get(RunRecord, run_id)
        row.runtime_version = "fake.v1"  # Legacy in-flight row must remain untouched.
        session.add(
            DshSessionLinkRecord(
                run_id=run_id,
                dsh_session_id="bound-session",
                request_hash="a" * 64,
                state="running",
                generation=1,
                upstream_identity_json=identity.model_dump_json(),
                plugin_build_hash=identity.plugin_build_hash,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    assert query.observability_view(run_id).versions.runtime_version == (
        f"dsh-web-{identity.source_version}+{identity.plugin_build_hash[:12]}"
    )
    assert db.get_run_record(run_id).runtime_version == "fake.v1"
    with db.session() as session:
        session.get(DshSessionLinkRecord, run_id).upstream_identity_json = json.dumps({})
    assert query.observability_view(run_id).versions.runtime_version == "unknown"


def test_legacy_unbound_failed_run_does_not_claim_fake_execution(tmp_path):
    db, _, run_id = _admitted_run(tmp_path)
    RunService(db).set_status(run_id, RunStatus.failed, error_code="dsh_unavailable")
    with db.session() as session:
        session.get(RunRecord, run_id).runtime_version = "fake.v1"
    assert (
        ResearchQueryService(db, pack_root=PACK_ROOT)
        .observability_view(run_id)
        .versions.runtime_version
        == "unknown"
    )
