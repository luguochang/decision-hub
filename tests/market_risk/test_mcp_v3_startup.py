"""Normal MCP factory with original private V3 preparation, no native/model calls."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from apps.research_mcp import main as mcp
from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe
from packages.kernel.decision_hub_kernel.application.research_context_selection_v4 import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.runtime_adapters.dsh_runtime.profile import PROMPT_POLICY_HASH
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import (
    prepare_runtime_turn_in_session,
    read_runtime_task_anchor,
)
from packages.workbench_adapters.research_mcp import RESEARCH_CATALOG_HTTP_PATH
from tests.dsh_native.test_execution_preparation_v2 import inputs
from tests.market_risk.test_recipe_selection_v3 import installation_fixture, manifest
from tests.market_risk.test_spot_research_v3_integration import (
    OWNER,
    prepared,
    spot_component_consumer,
    spot_context_provider,
    spot_event_text,
    spot_inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert all(
    x is not None
    for x in (
        inputs,
        installation_fixture,
        manifest,
        prepared,
        spot_component_consumer,
        spot_context_provider,
        spot_event_text,
        spot_inputs,
        forbid_external_connections,
    )
)


def _private_factory(monkeypatch, database, tmp_path, enabled):
    monkeypatch.setattr(mcp, "Database", lambda: database)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "mcp-private"))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_CAPABILITIES", ",".join(enabled))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", "1")
    monkeypatch.setenv("DECISION_HUB_DSH_RESEARCH_TOOL_KEY", "isolated-catalog")
    for key in (
        "DECISION_HUB_HARNESS_COMPARE_CONFIG",
        "DECISION_HUB_HARNESS_COMPARE_CONFIG_SHA256",
    ):
        monkeypatch.delenv(key, raising=False)


def _roles(database, run_id):
    with database.session() as session:
        return [
            (r.output_id, r.role, r.payload_json, r.payload_hash, r.schema_version)
            for r in session.scalars(
                select(RunRoleOutputRecord)
                .where(RunRoleOutputRecord.run_id == run_id)
                .order_by(RunRoleOutputRecord.role)
            )
        ]


def test_normal_factory_installs_actual_v3_before_scoped_catalog_original_read(
    spot_inputs,
    installation_fixture,
    monkeypatch,
    tmp_path,
):
    values, _ = spot_inputs
    db = values.database
    selected = ContextSelectionService(db).select(
        run_id=values.request.run_id, graph_round=1, expected_lease_owner=OWNER
    )
    with db.session() as session:
        anchor = read_runtime_task_anchor(session, values.request.run_id)
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        turn = prepare_runtime_turn_in_session(
            session,
            db,
            anchor,
            selected.request,
            values.upstream,
            runtime_id="dsh-web",
            runtime_version=(
                f"dsh-web-{values.upstream.source_version}+{values.upstream.plugin_build_hash[:12]}"
            ),
            profile_ref="decision-research.web.v1",
            prompt_policy_hash=PROMPT_POLICY_HASH,
            selection_reference=selected.reference,
            expected_lease_owner=OWNER,
        )
    before = _roles(db, values.request.run_id)
    relative, _, bodies = installation_fixture
    pointer_before = bodies[recipe.INSTALLED_POINTER_REF]
    monkeypatch.setattr(recipe, "_INSTALLED_V3", None)  # A genuinely fresh MCP process state.
    with pytest.raises(ValueError, match="research_graph_recipe_v3_not_installed"):
        ResearchMethodService(db).get(values.request.run_id)
    monkeypatch.setenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT", str(mcp.ROOT / relative))
    monkeypatch.setenv(
        "DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", hashlib.sha256(bodies[relative]).hexdigest()
    )
    # Only controlled validated receipt bytes; real V3 source/schema/pointer
    # verification is retained by installation_fixture's resource reader.
    managed_read = history._managed_read
    monkeypatch.setattr(
        history, "_managed_read", lambda ref: bodies[ref] if ref == relative else managed_read(ref)
    )
    _private_factory(monkeypatch, db, tmp_path, ["spot_market_facts", "official.macro"])
    server = mcp.create_server()  # Actual normal entry, actual durable scoped reader.
    with TestClient(server.streamable_http_app()) as client:
        result = client.get(
            RESEARCH_CATALOG_HTTP_PATH,
            params={"research_session_id": turn.prepared.link.dsh_session_id},
            headers={"x-decision-hub-bridge-key": "isolated-catalog"},
        )
        assert result.status_code == 200, result.text
        catalog = result.json()
        assert catalog["scope"]["status"] == "bound"
        assert catalog["scope"]["method_hash"] == turn.prepared.method.method_hash
        assert {r["capability_id"] for r in catalog["capabilities"]} == {
            "spot_market_facts",
            "official.macro",
        }
    with db.session() as session:
        original = read_prepared_execution_in_session(session, values.request.run_id)
    assert original == turn.prepared
    assert (
        original.execution.current_selection_row.row_schema_version
        == "research-context-selection.v4"
    )
    required = recipe._installation().manifest.source_files
    assert required  # The installed reader authenticated the complete original closure.
    protected = json.loads(dict((r[1], r[2]) for r in before)["graph_recipe_selection"])[
        "quality_context"
    ]["additional_required_context_refs"]
    assert protected and set(protected) <= set(original.execution.request.evidence_refs)
    assert any(
        ref.startswith("market-path-manifest:crypto:spot:okx:ETH-USDT:") for ref in protected
    )
    assert any(ref.startswith("research-run-spot-admission:") for ref in protected)
    assert _roles(db, values.request.run_id) == before
    assert bodies[recipe.INSTALLED_POINTER_REF] == pointer_before


def test_default_v2_normal_catalog_keeps_original_permissions_without_v3_installation(
    inputs,
    monkeypatch,
    tmp_path,
):
    original = inputs.prepare()
    before = _roles(inputs.database, inputs.request.run_id)
    monkeypatch.setattr(recipe, "_INSTALLED_V3", None)
    monkeypatch.delenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT", raising=False)
    monkeypatch.delenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", raising=False)
    _private_factory(monkeypatch, inputs.database, tmp_path, ["replay.research"])
    server = mcp.create_server()
    with TestClient(server.streamable_http_app()) as client:
        result = client.get(
            RESEARCH_CATALOG_HTTP_PATH,
            params={"research_session_id": original.link.dsh_session_id},
            headers={"x-decision-hub-bridge-key": "isolated-catalog"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["scope"]["method_hash"] == original.method.method_hash
        assert result.json()["capabilities"][0]["capability_id"] == "replay.research"
    assert recipe._INSTALLED_V3 is None
    assert _roles(inputs.database, inputs.request.run_id) == before


def test_startup_missing_pair_or_mismatched_receipt_rejects_before_database(monkeypatch):
    monkeypatch.setenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "1")
    monkeypatch.delenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT", raising=False)
    monkeypatch.delenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", raising=False)
    monkeypatch.setattr(mcp, "Database", lambda: pytest.fail("startup must reject before DB"))
    with pytest.raises(ValueError, match="research_graph_recipe_v3_startup_receipt_required"):
        mcp.create_server()
    monkeypatch.setenv(
        "DECISION_HUB_RETAINED_RECIPE_RECEIPT", str(mcp.ROOT / "artifacts/isolated/receipt.json")
    )
    with pytest.raises(ValueError, match="research_history_startup_receipt_pair_required"):
        mcp.create_server()
    monkeypatch.setenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", "0" * 64)
    monkeypatch.setattr(history, "_managed_read", lambda ref: b'{"untrusted":"changed"}')
    with pytest.raises(ValueError, match="research_history_startup_receipt_invalid"):
        mcp.create_server()
