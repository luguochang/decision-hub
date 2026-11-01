import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from apps.research_mcp.main import compose_capability_adapters, load_capability_manifests
from packages.provider_adapters.research import ReplayResearchArchive
from packages.workbench_adapters.research_catalog import research_query_catalog
from packages.workbench_adapters.research_mcp import (
    RESEARCH_CATALOG_HTTP_PATH,
    build_research_mcp_server,
)


def _catalog():
    manifests = load_capability_manifests()
    adapters = compose_capability_adapters(manifests, ReplayResearchArchive({}, {}))
    enabled = {"market.cross_asset", "market.crypto_derivatives", "market.crypto_crowding"}
    return manifests, adapters, enabled


def test_catalog_describes_actual_routes_and_filters_disabled_or_unaudited():
    manifests, adapters, enabled = _catalog()
    catalog = research_query_catalog(manifests, adapters, enabled)
    by_id = {row["capability_id"]: row for row in catalog["capabilities"]}
    assert set(by_id) == enabled
    yahoo = next(
        r
        for r in by_id["market.cross_asset"]["routes"]
        if r["provider_id"] == "yahoo-finance-public"
    )
    assert yahoo["allowed_domains"] == ["query1.finance.yahoo.com"]
    assert set(yahoo["fields"]) == {"level", "change_5d", "return_5d"}
    gate = next(
        r
        for r in by_id["market.crypto_derivatives"]["routes"]
        if r["provider_id"] == "gateio-derivatives-public"
    )
    assert "basis" in gate["fields"]
    assert not {"basis_rate", "venue", "source_url", "observed_at"} & set(gate["fields"])
    assert not gate["requires_event_window"]
    assert research_query_catalog(manifests, adapters, set())["capabilities"] == []
    changed = [m.model_copy(update={"audit_status": "pending"}) for m in manifests]
    assert research_query_catalog(changed, adapters, enabled)["capabilities"] == []
    router = next(a for a in adapters if a.capability_id == "market.cross_asset")
    original_hash = catalog["catalog_hash"]
    router.routes = tuple(r.model_copy(update={"audit_status": "pending"}) for r in router.routes)
    revised = research_query_catalog(manifests, adapters, enabled)
    assert (
        next(r for r in revised["capabilities"] if r["capability_id"] == "market.cross_asset")[
            "routes"
        ]
        == []
    )
    assert revised["catalog_hash"] != original_hash


def test_catalog_endpoint_requires_auth_and_never_calls_gateway():
    class NoExecution:
        async def execute(self, query):
            raise AssertionError("catalog must not execute providers")

    catalog = research_query_catalog(*_catalog())
    content = {k: v for k, v in catalog.items() if k != "catalog_hash"}
    assert (
        catalog["catalog_hash"]
        == hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    app = build_research_mcp_server(
        NoExecution(), bridge_key="test-only", query_catalog=catalog
    ).streamable_http_app()
    with TestClient(app) as client:
        assert client.get(RESEARCH_CATALOG_HTTP_PATH).status_code == 401
        assert (
            client.get(
                RESEARCH_CATALOG_HTTP_PATH, headers={"x-decision-hub-bridge-key": "wrong"}
            ).status_code
            == 401
        )
        result = client.get(
            RESEARCH_CATALOG_HTTP_PATH, headers={"x-decision-hub-bridge-key": "test-only"}
        )
        assert result.status_code == 200
        assert result.json() == catalog
    unavailable = build_research_mcp_server(
        NoExecution(), bridge_key="test-only"
    ).streamable_http_app()
    with TestClient(unavailable) as client:
        assert (
            client.get(
                RESEARCH_CATALOG_HTTP_PATH, headers={"x-decision-hub-bridge-key": "test-only"}
            ).status_code
            == 503
        )


def _scope_database(tmp_path, *, method=True):
    from packages.contracts_py.decision_hub_contracts import (
        DshSessionSubmit,
        DshUpstreamIdentity,
    )
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
        DshSessionLinkService,
    )
    from packages.kernel.decision_hub_kernel.application.research_method import (
        ResearchMethodService,
    )
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.kernel.decision_hub_kernel.persistence.db import Database

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'scope.sqlite3'}")
    db.create_all()
    now = datetime.now(UTC)
    run_id, created = RunService(db, clock=lambda: now).create(
        "event-catalog-scope", strategy_version="research.v1"
    )
    assert created
    request_hash = "a" * 64
    session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, request_hash)
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run_id,
        request_hash=request_hash,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=now + timedelta(minutes=5),
        model_step_timeout_ms=60_000,
        max_tool_calls=12,
        generation=1,
    )
    upstream = DshUpstreamIdentity(
        source_commit="1" * 40,
        source_version="0.1.0",
        package_versions={},
        plugin_build_hash="2" * 64,
    )
    DshSessionLinkService(db, clock=lambda: now).reserve(submit, upstream)
    if method:
        ResearchMethodService(db).freeze(
            run_id,
            {
                "upstream_identity": upstream.model_dump(mode="json"),
                "allowed_capabilities": ["market.cross_asset"],
                "execution_mode": "live",
            },
            request_hash=submit.request_hash,
        )
    return db, submit


def _live_catalog_inputs(tmp_path: Path):
    """Freeze an actual live Task before building its Method and Link."""
    from apps.hub_worker.research import ResearchRequestFactory
    from packages.contracts_py.decision_hub_contracts import (
        DshSessionSubmit,
        DshUpstreamIdentity,
    )
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
        DshSessionLinkService,
    )
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
    )
    from packages.kernel.decision_hub_kernel.application.research_request_identity import (
        research_request_hash,
    )
    from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
    from tests.evolution.test_research_worker import (
        _admitted_run,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.research.test_full_method_v2_bundle import (
        _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    )

    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        request = ResearchRequestFactory(
            database,
            pack_root=root,
            execution_mode="live",
            allowed_capabilities=("web.search",),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        ).build(event_id, run_id)
        digest = research_request_hash(request)
        session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, digest)
        submit = DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash=digest,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=request.deadline_at,
            max_tool_calls=request.execution_budget.max_tool_calls,
            model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
            generation=1,
        )
        upstream = DshUpstreamIdentity(
            source_commit="1" * 40,
            source_version="0.1.0",
            package_versions={},
            plugin_build_hash="2" * 64,
        )
        yield PreparedInputs(database, request, submit, upstream, "Unused", datetime.now(UTC))
    finally:
        database.engine.dispose()


def _v3_scope_database(tmp_path: Path, *, live=False):
    """Create a contract-valid V3 Method and Link without calling a Provider."""
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
        DshSessionLinkService,
    )
    from packages.kernel.decision_hub_kernel.application.research_context_execution import (
        build_method_v3_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.research_context_selection import (
        ContextSelectionReference,
    )
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
        freeze_method_v2_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.research_method import recipe_hash
    from packages.kernel.decision_hub_kernel.persistence.db import DshSessionPromptRecord
    from tests.orchestration.test_graph_observed_result_v2 import (
        _prepared_inputs,  # pyright: ignore[reportPrivateUsage]
    )

    owner = (
        _live_catalog_inputs(tmp_path)
        if live
        else _prepared_inputs(tmp_path, max_rounds=1, max_repairs=0)
    )
    fixture = next(owner)
    try:
        request_hash = recipe_hash(fixture.request.model_dump(mode="json"))
        selection_hash = hashlib.sha256(
            f"catalog-selection\0{fixture.request.run_id}".encode()
        ).hexdigest()
        reference = ContextSelectionReference(
            output_id="ctxsel_" + selection_hash,
            run_id=fixture.request.run_id,
            role="context_select.r1.a1",
            payload_hash=selection_hash,
            recorded_at=fixture.now,
        )
        selection = SimpleNamespace(
            reference=reference,
            receipt=SimpleNamespace(
                selector_identity=SimpleNamespace(
                    ranking_rules_ref="research-context.relevance-authority-recency.v2"
                ),
                selection_payload_hash=selection_hash,
                current_full_request_hash=request_hash,
            ),
        )
        runtime_version = (
            f"dsh-web-{fixture.upstream.source_version}"
            f"+{fixture.upstream.plugin_build_hash[:12]}"
        )
        with fixture.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            method = build_method_v3_in_session(
                session,
                fixture.request,
                fixture.submit,
                fixture.upstream,
                selection=selection,
                runtime_id="dsh-web",
                runtime_version=runtime_version,
                profile_ref="decision-research",
                prompt_policy_hash="d" * 64,
                recorded_at=fixture.now,
            )
            freeze_method_v2_in_session(session, method)
            DshSessionLinkService(fixture.database).reserve_in_session(
                session,
                fixture.submit,
                fixture.upstream,
                recorded_at=fixture.now,
            )
            session.add(
                DshSessionPromptRecord(
                    run_id=fixture.request.run_id,
                    generation=fixture.submit.generation,
                    dsh_session_id=fixture.submit.deterministic_session_id,
                    request_id=fixture.submit.deterministic_request_id,
                    request_hash=fixture.submit.request_hash,
                    prompt="catalog scope fixture",
                    created_at=fixture.now,
                )
            )
        return owner, fixture
    except Exception:
        owner.close()
        raise


@pytest.mark.parametrize("shift_link_deadline", [False, True], ids=["exact", "mismatch"])
def test_live_scoped_catalog_authenticates_task_deadline_and_hash(
    tmp_path, shift_link_deadline
):
    from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord
    from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

    owner, fixture = _v3_scope_database(tmp_path, live=True)
    try:
        assert fixture.request.execution_mode == "live"
        catalog = research_query_catalog(*_catalog())
        assert "scope" not in catalog
        if shift_link_deadline:
            with fixture.database.session() as session:
                link = session.get(DshSessionLinkRecord, fixture.request.run_id)
                assert link is not None
                link.deadline_at += timedelta(seconds=1)
            with pytest.raises(ValueError, match="link_method_mismatch"):
                scoped_research_query_catalog(
                    catalog, fixture.database, fixture.submit.deterministic_session_id
                )
            return

        scoped = scoped_research_query_catalog(
            catalog, fixture.database, fixture.submit.deterministic_session_id
        )
        assert scoped["scope"]["execution_mode"] == "live"
        assert scoped["scope"]["deadline_at"] == fixture.request.deadline_at.isoformat()
        assert scoped["scope"]["deadline_at"] == fixture.submit.deadline_at.isoformat()
        payload = {key: value for key, value in scoped.items() if key != "catalog_hash"}
        assert scoped["catalog_hash"] == hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        payload["scope"] = {
            **payload["scope"],
            "deadline_at": (fixture.request.deadline_at + timedelta(seconds=1)).isoformat(),
        }
        assert scoped["catalog_hash"] != hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    finally:
        owner.close()


@pytest.fixture(scope="module")
def _v3_catalog_tamper_scope(tmp_path_factory):
    """Reuse one real frozen V3 scope across the narrow tamper matrix."""
    owner, fixture = _v3_scope_database(tmp_path_factory.mktemp("catalog-v3-tamper"))
    manifests, adapters, _enabled = _catalog()
    catalog = research_query_catalog(
        manifests, adapters, set(fixture.request.allowed_capabilities)
    )
    try:
        yield fixture, catalog
    finally:
        owner.close()


@pytest.mark.parametrize(
    ("tamper", "expected_error"),
    [
        ("generation", "link_method_mismatch"),
        ("request_hash", "link_method_mismatch"),
        ("method_row_time", "scope_receipt_invalid"),
        ("deadline", "link_method_mismatch"),
        ("budget", "link_method_mismatch"),
        ("execution_mode", "method_pack_mismatch"),
        ("pack_row_identity", "method_pack_mismatch"),
    ],
    ids=[
        "generation",
        "request",
        "row-time",
        "deadline",
        "budget",
        "execution-mode",
        "pack-row-identity",
    ],
)
def test_v3_scoped_catalog_rejects_frozen_scope_tampering(
    _v3_catalog_tamper_scope, tamper, expected_error
):
    """Every frozen field used by the light projection remains fail closed."""
    from packages.kernel.decision_hub_kernel.application.research_method import (
        canonical_recipe,
        recipe_hash,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import (
        DshSessionLinkRecord,
        RunRoleOutputRecord,
    )
    from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

    fixture, catalog = _v3_catalog_tamper_scope
    with fixture.database.session() as session:
        link_row = session.get(DshSessionLinkRecord, fixture.request.run_id)
        assert link_row is not None
        method_row = session.query(RunRoleOutputRecord).filter_by(
            run_id=fixture.request.run_id, role="research_execution_method"
        ).one()
        pack_row = session.query(RunRoleOutputRecord).filter_by(
            run_id=fixture.request.run_id, role="research_request_pack"
        ).one()
        saved_link = {
            "generation": link_row.generation,
            "request_hash": link_row.request_hash,
            "deadline_at": link_row.deadline_at,
            "max_tool_calls": link_row.max_tool_calls,
        }
        saved_method = (
            method_row.payload_json,
            method_row.payload_hash,
            method_row.created_at,
        )
        saved_pack = (pack_row.payload_json, pack_row.payload_hash, pack_row.created_at)

        if tamper == "generation":
            link_row.generation += 1
        elif tamper == "request_hash":
            link_row.request_hash = "f" * 64
        elif tamper == "method_row_time":
            method_row.created_at += timedelta(seconds=1)
        elif tamper == "deadline":
            link_row.deadline_at += timedelta(seconds=1)
        elif tamper == "budget":
            link_row.max_tool_calls -= 1
        elif tamper == "execution_mode":
            method_body = json.loads(method_row.payload_json)
            method_body["definition"]["execution_mode"] = "live"
            method_body["method_hash"] = recipe_hash(method_body["definition"])
            method_row.payload_json = canonical_recipe(method_body)
            method_row.payload_hash = hashlib.sha256(method_row.payload_json.encode()).hexdigest()
        elif tamper == "pack_row_identity":
            pack_row.created_at += timedelta(seconds=1)
        else:  # pragma: no cover - the parameter table is exhaustive
            raise AssertionError(f"unknown tamper case: {tamper}")

    try:
        with pytest.raises(ValueError, match=expected_error):
            scoped_research_query_catalog(
                catalog, fixture.database, fixture.submit.deterministic_session_id
            )
    finally:
        with fixture.database.session() as session:
            link_row = session.get(DshSessionLinkRecord, fixture.request.run_id)
            assert link_row is not None
            for name, value in saved_link.items():
                setattr(link_row, name, value)
            method_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_execution_method"
            ).one()
            method_row.payload_json, method_row.payload_hash, method_row.created_at = saved_method
            pack_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_request_pack"
            ).one()
            pack_row.payload_json, pack_row.payload_hash, pack_row.created_at = saved_pack


def test_scoped_catalog_rejects_legacy_method_and_preserves_global_catalog(tmp_path):
    from copy import deepcopy

    from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

    db, submit = _scope_database(tmp_path)
    catalog = research_query_catalog(*_catalog())
    original = deepcopy(catalog)
    with pytest.raises(ValueError, match="method_v3_required"):
        scoped_research_query_catalog(catalog, db, submit.deterministic_session_id)
    assert catalog == original
    db.engine.dispose()


def test_legacy_catalog_does_not_borrow_current_permissions(tmp_path):
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

    db, submit = _scope_database(tmp_path, method=False)
    with pytest.raises(ValueError, match="method_v3_required"):
        scoped_research_query_catalog(
            research_query_catalog(*_catalog()), db, submit.deterministic_session_id
        )
    with db.session() as s:
        assert s.query(RunRoleOutputRecord).count() == 0
    db.engine.dispose()


def test_scoped_catalog_rejects_wrong_session_or_corrupt_method(tmp_path):
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

    db, submit = _scope_database(tmp_path)
    catalog = research_query_catalog(*_catalog())
    with pytest.raises(ValueError, match="session_unknown"):
        scoped_research_query_catalog(catalog, db, "unknown")
    with db.session() as s:
        row = s.query(RunRoleOutputRecord).filter_by(role="research_execution_method").one()
        row.payload_json = row.payload_json.replace("market.cross_asset", "official.macro")
    with pytest.raises(ValueError, match="method_v3_required"):
        scoped_research_query_catalog(catalog, db, submit.deterministic_session_id)
    db.engine.dispose()


def test_scoped_http_catalog_auth_and_legacy_binding_are_fail_closed(tmp_path):
    from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

    db, submit = _scope_database(tmp_path)

    class ScopedGateway:
        def scoped_query_catalog(self, catalog, session_id):
            return scoped_research_query_catalog(catalog, db, session_id)

        async def execute(self, query):
            raise AssertionError("catalog cannot invoke provider")

    app = build_research_mcp_server(
        ScopedGateway(), bridge_key="test-only", query_catalog=research_query_catalog(*_catalog())
    ).streamable_http_app()
    with TestClient(app) as client:
        params = {"research_session_id": submit.deterministic_session_id}
        assert client.get(RESEARCH_CATALOG_HTTP_PATH, params=params).status_code == 401
        headers = {"x-decision-hub-bridge-key": "test-only"}
        result = client.get(RESEARCH_CATALOG_HTTP_PATH, params=params, headers=headers)
        assert result.status_code == 409
        assert (
            client.get(
                RESEARCH_CATALOG_HTTP_PATH,
                headers=headers,
                params={"research_session_id": "unknown"},
            ).status_code
            == 409
        )
    db.engine.dispose()


def test_durable_gateway_preserves_frozen_harness_catalog_precedence(monkeypatch):
    """The ordinary V3 projection cannot replace a signed harness catalog."""
    from packages.workbench_adapters import research_catalog
    from packages.workbench_adapters.durable_research_gateway import (
        DurableResearchCapabilityGateway,
    )

    frozen = {"schema_version": "research-query-catalog.v1", "capabilities": []}
    calls = []

    class FrozenSource:
        def scoped_query_catalog(self, catalog, session_id):
            calls.append((catalog, session_id))
            return frozen

    def ordinary_forbidden(*_args, **_kwargs):
        raise AssertionError("signed harness catalog fell through to ordinary V3 projection")

    monkeypatch.setattr(research_catalog, "scoped_research_query_catalog", ordinary_forbidden)
    gateway = object.__new__(DurableResearchCapabilityGateway)
    gateway.frozen_source = FrozenSource()
    gateway.evidence = SimpleNamespace(database=object())
    catalog = {"schema_version": "research-query-catalog.v1", "capabilities": [object()]}

    assert gateway.scoped_query_catalog(catalog, "dsh-harness") is frozen
    assert calls == [(catalog, "dsh-harness")]


def test_v3_scoped_catalog_is_bounded_read_only_and_fail_closed(tmp_path, monkeypatch):
    """A large ledger stays fast and no catalog result can create tool/provider work."""
    from sqlalchemy import insert

    from packages.kernel.decision_hub_kernel.application import (
        research_binding,
        research_execution_binding,
    )
    from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
    from packages.kernel.decision_hub_kernel.persistence.db import (
        DshSessionLinkRecord,
        ResearchToolCallReservationRecord,
        RunRoleOutputRecord,
    )
    from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

    owner, fixture = _v3_scope_database(tmp_path)
    try:
        manifests, adapters, _enabled = _catalog()
        catalog = research_query_catalog(
            manifests, adapters, set(fixture.request.allowed_capabilities)
        )
        assert [item["capability_id"] for item in catalog["capabilities"]] == [
            "replay.research"
        ]
        with fixture.database.session() as session:
            session.execute(
                insert(RunRoleOutputRecord),
                [
                    {
                        "output_id": f"catalog_noise_{number:05d}",
                        "run_id": f"catalog_noise_run_{number:05d}",
                        "role": "catalog_noise",
                        "payload_json": "{}",
                        "payload_hash": hashlib.sha256(b"{}").hexdigest(),
                        "schema_version": "catalog-noise.v1",
                        "created_at": fixture.now,
                    }
                    for number in range(20_000)
                ],
            )

        def forbidden(*_args, **_kwargs):
            raise AssertionError("scoped catalog repeated a deep original validation")

        monkeypatch.setattr(
            research_execution_binding, "read_prepared_execution_in_session", forbidden
        )
        monkeypatch.setattr(research_binding, "read_research_bundle_in_session", forbidden)
        monkeypatch.setattr(research_binding, "_validate_current_source", forbidden)

        # Factory/service composition above also performs SQLAlchemy's one-time
        # mapper setup. Gate a warmed request separately from cold process
        # import cost; the transport retains its independent 30 second cap.
        warmed = scoped_research_query_catalog(
            catalog, fixture.database, fixture.submit.deterministic_session_id
        )
        started = time.perf_counter()
        scoped = scoped_research_query_catalog(
            catalog, fixture.database, fixture.submit.deterministic_session_id
        )
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0
        assert scoped == warmed
        assert [item["capability_id"] for item in scoped["capabilities"]] == [
            "replay.research"
        ]
        assert scoped["scope"] == {
            "run_id": fixture.request.run_id,
            "dsh_session_id": fixture.submit.deterministic_session_id,
            "request_hash": fixture.submit.request_hash,
            "generation": 1,
            "status": "bound",
            "method_hash": scoped["scope"]["method_hash"],
            "execution_mode": "replay",
            "deadline_at": fixture.request.deadline_at.isoformat(),
        }
        assert all(item["modes"] == ["replay"] for item in scoped["capabilities"])
        assert all(
            route["modes"] == ["replay"]
            for item in scoped["capabilities"]
            for route in item["routes"]
        )

        provider_calls = 0

        class ScopedGateway:
            def scoped_query_catalog(self, value, session_id):
                return scoped_research_query_catalog(value, fixture.database, session_id)

            async def execute(self, _query):
                nonlocal provider_calls
                provider_calls += 1
                raise AssertionError("catalog GET invoked a provider")

        app = build_research_mcp_server(
            ScopedGateway(), bridge_key="test-only", query_catalog=catalog
        ).streamable_http_app()
        with TestClient(app) as client:
            response = client.get(
                RESEARCH_CATALOG_HTTP_PATH,
                params={"research_session_id": fixture.submit.deterministic_session_id},
                headers={"x-decision-hub-bridge-key": "test-only"},
            )
            assert response.status_code == 200
            assert response.json() == scoped
        assert provider_calls == 0

        with fixture.database.session() as session:
            method_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_execution_method"
            ).one()
            pack_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_request_pack"
            ).one()
            link_row = session.get(DshSessionLinkRecord, fixture.request.run_id)
            assert link_row is not None
            saved_method = (method_row.payload_json, method_row.payload_hash)
            saved_pack = (pack_row.payload_json, pack_row.payload_hash)
            saved_plugin_hash = link_row.plugin_build_hash

            # A self-consistent Method row still cannot widen rights beyond the
            # independently frozen request Pack.
            method_body = json.loads(method_row.payload_json)
            method_body["definition"]["allowed_capabilities"].append("official.macro")
            method_body["definition"]["allowed_capabilities"].sort()
            method_body["method_hash"] = hashlib.sha256(
                canonical_recipe(method_body["definition"]).encode()
            ).hexdigest()
            method_row.payload_json = canonical_recipe(method_body)
            method_row.payload_hash = hashlib.sha256(
                method_row.payload_json.encode()
            ).hexdigest()
        with pytest.raises(ValueError, match="method_pack_mismatch"):
            scoped_research_query_catalog(
                catalog, fixture.database, fixture.submit.deterministic_session_id
            )

        with fixture.database.session() as session:
            method_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_execution_method"
            ).one()
            method_row.payload_json, method_row.payload_hash = saved_method
            pack_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_request_pack"
            ).one()
            pack_body = json.loads(pack_row.payload_json)
            pack_body["definition_hash"] = "0" * 64
            pack_row.payload_json = canonical_recipe(pack_body)
            pack_row.payload_hash = hashlib.sha256(pack_row.payload_json.encode()).hexdigest()
        with pytest.raises(ValueError, match="method_pack_mismatch"):
            scoped_research_query_catalog(
                catalog, fixture.database, fixture.submit.deterministic_session_id
            )

        with fixture.database.session() as session:
            pack_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_request_pack"
            ).one()
            pack_row.payload_json, pack_row.payload_hash = saved_pack
            link_row = session.get(DshSessionLinkRecord, fixture.request.run_id)
            assert link_row is not None
            link_row.plugin_build_hash = "0" * 64
        with pytest.raises(ValueError, match="link_method_mismatch"):
            scoped_research_query_catalog(
                catalog, fixture.database, fixture.submit.deterministic_session_id
            )

        with fixture.database.session() as session:
            link_row = session.get(DshSessionLinkRecord, fixture.request.run_id)
            assert link_row is not None
            link_row.plugin_build_hash = saved_plugin_hash
            method_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_execution_method"
            ).one()
            method_row.payload_hash = "0" * 64
        with pytest.raises(ValueError, match="scope_receipt_invalid"):
            scoped_research_query_catalog(
                catalog, fixture.database, fixture.submit.deterministic_session_id
            )

        with fixture.database.session() as session:
            method_row = session.query(RunRoleOutputRecord).filter_by(
                run_id=fixture.request.run_id, role="research_execution_method"
            ).one()
            method_row.payload_hash = saved_method[1]
            assert session.query(ResearchToolCallReservationRecord).count() == 0
        assert provider_calls == 0
    finally:
        owner.close()


def test_route_symbols_follow_actual_adapter_allowlists_and_configuration():
    from packages.provider_adapters.macro_market.fred import FredSeriesResearchAdapter
    from packages.provider_adapters.macro_market.yahoo import _resolve_series
    from packages.provider_adapters.market.crowding import _symbol
    from packages.provider_adapters.market.gate_htx_research import _gate_contract, _htx_contract
    from packages.workbench_adapters.research_catalog import _query_inputs

    catalog = research_query_catalog(*_catalog())
    routes = {r["provider_id"]: r for c in catalog["capabilities"] for r in c["routes"]}
    yahoo = routes["yahoo-finance-public"]
    assert yahoo["symbols_required"] is True
    assert {"DXY", "US10Y", "^GSPC"} <= set(yahoo["symbols"])
    assert not {"QQQ", "WALCL", "DGS2"} & set(yahoo["symbols"])
    for symbol in yahoo["symbols"]:
        assert len(_resolve_series([symbol])) == 1
    for provider, resolver in [
        ("gateio-derivatives-public", _gate_contract),
        ("htx-derivatives-public", _htx_contract),
    ]:
        route = routes[provider]
        assert route["symbols_required"] and route["max_symbols"] == 1
        assert all(resolver([symbol]) for symbol in route["symbols"])
    # The standalone crowding metadata is derived from its actual resolver.
    from packages.provider_adapters.market.crowding import CryptoCrowdingResearchAdapter

    assert all(_symbol([s]) for s in CryptoCrowdingResearchAdapter.supported_symbols)
    fred = FredSeriesResearchAdapter(series_allowlist=frozenset({"CUSTOM"}))
    assert _query_inputs(fred)["symbols"] == ["CUSTOM"]
    assert _query_inputs(object())["symbols"] is None


def test_document_catalog_matches_real_source_policy_and_browser_scope():
    manifests, adapters, _ = _catalog()
    catalog = research_query_catalog(manifests, adapters, {"web.fetch", "browser.readback"})
    by_id = {c["capability_id"]: c["routes"][0] for c in catalog["capabilities"]}
    browser = next(a for a in adapters if a.capability_id == "browser.readback")
    for rule in by_id["browser.readback"]["sources"]:
        for requirement in rule["requirement_ids"]:
            for path in rule["allowed_paths"]:
                policy, _ = browser._source_for_url(
                    "https://" + rule["domains"][0] + path + "probe", requirement
                )
                assert policy.source_ref == rule["source_ref"]
    fetch_sources = by_id["web.fetch"]["sources"]
    fed = next(r for r in fetch_sources if r["source_ref"] == "fed.monetary_policy")
    assert "market_narrative" in fed["requirement_ids"]
    assert "macro_transmission" not in fed["requirement_ids"]
    assert "/newsevents/speech/" in fed["allowed_paths"]
    assert all(
        "/newsevents/speech/" not in r["allowed_paths"]
        for r in by_id["browser.readback"]["sources"]
    )
    assert by_id["web.fetch"]["target_url_required"] is True
    assert len(json.dumps(catalog).encode()) < 64000


def test_sources_are_intersected_with_capability_and_route_domain_scopes():
    from types import SimpleNamespace

    from packages.workbench_adapters.research_catalog import _query_inputs

    adapter = SimpleNamespace(
        source_query_catalog=[
            {
                "source_ref": "one",
                "domains": ["example.com", "outside.test"],
                "allowed_paths": ["/news/"],
                "requirement_ids": ["market_narrative"],
            }
        ]
    )
    result = _query_inputs(adapter, ["example.com"], ["api.example.com"])
    assert result["sources"][0]["domains"] == ["api.example.com"]
    assert adapter.source_query_catalog[0]["domains"] == ["example.com", "outside.test"]
    assert _query_inputs(adapter, ["unrelated.test"])["sources"] == []
    manifests, adapters, _ = _catalog()
    catalog = research_query_catalog(manifests, adapters, {"official.macro"})
    row = catalog["capabilities"][0]
    domains = {d for source in row["routes"][0]["sources"] for d in source["domains"]}
    assert domains <= set(row["allowed_domains"])
    assert "api.alternative.me" not in domains
