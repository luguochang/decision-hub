"""Candidate contracts and SQLite writer gates, with controlled installation proof fixtures.

The in-memory fixture represents the subset returned after a trusted product guard;
it does not execute that guard, install a pointer, attest a Host, or admit a spot Study.
Original bundle authentication is separately a Root integration gate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application import research_graph_recipe as old
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.application.research_quality_projection import (
    QualityProjectionContextV1,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

NOW = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"


def baseline():
    return QualityProjectionContextV1(
        scope_id=SCOPE,
        additional_required_context_refs=("numeric-risk", "latest-event", "boundary"),
    )


@pytest.fixture(scope="module")
def manifest():
    return recipe.graph_recipe_manifest_v3()


def selection(manifest):
    context = baseline()
    arguments = dict(
        run_id="run-contract",
        original_task_ref=ObservedExecutionRef(
            output_id="task",
            role=old.TASK_ROLE,
            schema_version="research-task-request.v2",
            payload_hash="1" * 64,
            recorded_at=NOW,
        ),
        original_pack_ref=ObservedExecutionRef(
            output_id="pack",
            role=old.PACK_ROLE,
            schema_version="research-request-pack.v2",
            payload_hash="2" * 64,
            recorded_at=NOW,
        ),
        manifest=manifest,
        dependency_hash=observation_sha(manifest.model_dump(mode="json")),
        installation_receipt=recipe.GraphRecipeResourceV3(
            path="artifacts/fixture/receipt.json", sha256="3" * 64
        ),
        installed_pointer=recipe.GraphRecipeResourceV3(
            path=recipe.INSTALLED_POINTER_REF, sha256="4" * 64
        ),
        quality_context=context,
        quality_context_hash=observation_sha(context.model_dump(mode="json")),
        recorded_at=NOW,
    )
    temporary = recipe.GraphRecipeSelectionV3.model_construct(**arguments, effective_hash="0" * 64)
    return recipe.GraphRecipeSelectionV3(
        **arguments, effective_hash=recipe._effective_hash(temporary)
    )


@pytest.fixture
def installation_fixture(monkeypatch, manifest):
    """Controlled bytes only; no installation pointer written to the actual repo."""
    pointer = recipe.GraphRecipeInstalledPointerV3(
        registration=manifest.registration,
        dependency_hash=observation_sha(manifest.model_dump(mode="json")),
        plugin_build_hash="a" * 64,
    )
    pointer_raw = observation_json(pointer.model_dump(mode="json")).encode()
    receipt_path = "artifacts/fixture-installed-v3/receipt.json"
    sources = {
        item.path: item.payload_hash for item in (*manifest.source_files, *manifest.schema_files)
    }
    sources[manifest.registration.path] = manifest.registration.sha256
    sources[recipe.INSTALLED_POINTER_REF] = hashlib.sha256(pointer_raw).hexdigest()
    receipt = {
        "schema": "product-validated-builds.v1",
        "repository_root": str(recipe._ROOT),
        "sources": sources,
        "artifacts": {"fixture-only": "b" * 64},
        "plugin_build_hash": pointer.plugin_build_hash,
        "evidence": {
            "research_graph_recipe_v3": {
                "installed_pointer": {
                    "path": recipe.INSTALLED_POINTER_REF,
                    "sha256": sources[recipe.INSTALLED_POINTER_REF],
                }
            }
        },
    }
    receipt_raw = observation_json(receipt).encode()
    bodies = {receipt_path: receipt_raw, recipe.INSTALLED_POINTER_REF: pointer_raw}
    original_read = recipe._read_resource
    monkeypatch.setattr(
        recipe, "_read_resource", lambda ref: bodies[ref] if ref in bodies else original_read(ref)
    )
    monkeypatch.setattr(recipe, "_INSTALLED_V3", None)
    return receipt_path, receipt, bodies


def configure(fixture):
    relative, _, bodies = fixture
    recipe.configure_graph_recipe_v3_from_validated_release(
        receipt_path=recipe._ROOT / relative,
        receipt_sha256=hashlib.sha256(bodies[relative]).hexdigest(),
    )


def test_manifest_binds_actual_v3_quality_projection_and_registration_resources(manifest):
    paths = {item.path for item in manifest.source_files}
    assert recipe.SOURCE_REF in paths
    assert (
        "packages/kernel/decision_hub_kernel/application/research_graph_projection_v3.py" in paths
    )
    assert "packages/kernel/decision_hub_kernel/application/research_quality_projection.py" in paths
    assert "packages/kernel/decision_hub_kernel/decision/research_quality.py" in paths
    assert recipe.REGISTRATION_REF == manifest.registration.path
    assert {recipe.CONTEXT_SCHEMA_REF, recipe.QUALITY_SCHEMA_REF}.issubset(
        {item.path for item in manifest.schema_files}
    )
    assert recipe.GraphRecipeRegistrationV3().lifecycle == "candidate"
    assert recipe.GraphRecipeRegistrationV3().auto_install is False


def test_selection_context_and_effective_hash_round_trip(manifest):
    original = selection(manifest)
    assert recipe.GraphRecipeSelectionV3.model_validate_json(original.model_dump_json()) == original
    changed = original.model_dump(mode="json")
    changed["quality_context"]["additional_required_context_refs"].append("new-input")
    changed["quality_context_hash"] = observation_sha(changed["quality_context"])
    with pytest.raises(ValueError, match="selection_binding_invalid"):
        recipe.GraphRecipeSelectionV3.model_validate_json(observation_json(changed))


@pytest.mark.parametrize(
    "field,value",
    [
        ("bound_result_hash", "a" * 64),
        ("runtime_attestation_ref", "model-said-pass"),
        ("effective_budget_ref", "model-said-budget"),
        ("assessed_at", NOW),
        ("selection_manifest_hash", "b" * 64),
        ("context_fidelity_verified", True),
        ("path_risk_model_qualified", True),
        ("execution_support_verified", True),
        ("monitor_state", "active"),
        ("additional_required_context_refs", ()),
    ],
)
def test_preexecution_context_rejects_future_model_attestation_claims(field, value):
    context = baseline().model_copy(update={field: value})
    with pytest.raises(ValueError, match="preexecution_claim_forbidden"):
        recipe._require_preexecution_context(context)


def test_unconfigured_candidate_cannot_enable_v3(monkeypatch):
    monkeypatch.setattr(recipe, "_INSTALLED_V3", None)
    with pytest.raises(ValueError, match="not_installed"):
        recipe._installation()


def test_actual_subset_parser_requires_installed_pointer_and_complete_sources(installation_fixture):
    relative, receipt, bodies = installation_fixture
    receipt["evidence"] = {}
    bodies[relative] = observation_json(receipt).encode()
    with pytest.raises(ValueError, match="installed_pointer_required"):
        configure(installation_fixture)
    assert recipe._INSTALLED_V3 is None


def test_changed_runtime_or_quality_dependency_rejects_installed_identity(installation_fixture):
    relative, receipt, bodies = installation_fixture
    receipt["sources"][recipe.SOURCE_REF] = "0" * 64
    bodies[relative] = observation_json(receipt).encode()
    with pytest.raises(ValueError, match="effective_source_not_validated"):
        configure(installation_fixture)


def test_pointer_is_freshly_verified_and_process_cannot_rebind(installation_fixture):
    configure(installation_fixture)
    relative, receipt, bodies = installation_fixture
    recipe._installation()
    old_pointer = bodies[recipe.INSTALLED_POINTER_REF]
    bodies[recipe.INSTALLED_POINTER_REF] = old_pointer + b" "
    with pytest.raises(ValueError, match="installed_pointer_changed"):
        recipe._installation()
    bodies[recipe.INSTALLED_POINTER_REF] = old_pointer
    receipt["artifacts"]["other-fixture"] = "c" * 64
    bodies[relative] = observation_json(receipt).encode()
    with pytest.raises(ValueError, match="process_rebind_forbidden"):
        configure(installation_fixture)


def test_import_syntax_cache_reparses_changed_body_and_never_caches_source_authority(
    monkeypatch, installation_fixture
):
    relative = "packages/kernel/decision_hub_kernel/application/example.py"
    original = b"from . import research_method\n"
    changed = b"from . import research_quality_projection\n"
    names = recipe._import_names(relative, original, hashlib.sha256(original).hexdigest())
    assert any(name.endswith("research_method") for name in names)
    updated = recipe._import_names(relative, changed, hashlib.sha256(changed).hexdigest())
    assert any(name.endswith("research_quality_projection") for name in updated)
    assert names != updated
    with pytest.raises(SyntaxError):
        recipe._import_names(relative, b"from invalid (", "f" * 64)
    configure(installation_fixture)
    recipe._installation()
    actual_read = recipe._read_resource
    monkeypatch.setattr(
        recipe,
        "_read_resource",
        lambda ref: (
            actual_read(ref) + b"\n# changed\n"
            if ref.endswith("/research_quality_projection.py")
            else actual_read(ref)
        ),
    )
    with pytest.raises(ValueError, match="effective_implementation_mismatch"):
        recipe._installation()


def test_round_quality_binding_preserves_frozen_scoped_required_inputs(manifest):
    from packages.kernel.decision_hub_kernel.application.research_graph_projection_v3 import (
        quality_for_state,
    )
    from tests.market_risk.test_quality_projection_v3 import material

    _, _, state, context = material()
    context = context.model_copy(update={"scope_id": SCOPE})
    quality = quality_for_state(state, quality_context=context)
    frozen = selection(manifest).model_copy(update={"run_id": quality.run_id})
    # Re-seal the test selection's run identity; source report keeps its own hash.
    frozen = recipe.GraphRecipeSelectionV3.model_validate_json(
        observation_json(
            {**frozen.model_dump(mode="json"), "effective_hash": recipe._effective_hash(frozen)}
        )
    )
    hashes = recipe.verify_round_quality_binding_v3(frozen, context, quality)
    assert hashes == (observation_sha(context.model_dump(mode="json")), quality.decision_hash)
    with pytest.raises(ValueError, match="round_quality_binding_invalid"):
        recipe.verify_round_quality_binding_v3(
            frozen,
            context.model_copy(update={"additional_required_context_refs": ("numeric-risk",)}),
            quality,
        )


@pytest.fixture
def writer_fixture(tmp_path, monkeypatch):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'recipe-contract.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="run-contract",
                event_id="event-contract",
                status="admitted",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    def original_rows(session, run_id):
        rows = list(
            session.scalars(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role.in_([old.TASK_ROLE, old.PACK_ROLE]),
                )
            )
        )
        by_role = {row.role: row for row in rows}
        return by_role[old.TASK_ROLE], by_role[old.PACK_ROLE]

    # Unit boundary: emulate separately authenticated bundle rows. No caller
    # outside the test bypasses the production old._original_rows reader.
    monkeypatch.setattr(old, "_original_rows", original_rows)
    yield database
    database.engine.dispose()


def add_originals(session, *, instrument="BTC-USDT"):
    for role, schema, body in (
        (
            old.TASK_ROLE,
            "research-task-request.v2",
            {"scope": {"market": "crypto", "instrument": instrument}},
        ),
        (old.PACK_ROLE, "research-request-pack.v2", {"pack": "controlled-contract-fixture"}),
    ):
        raw = observation_json(body)
        session.add(
            RunRoleOutputRecord(
                output_id=role,
                run_id="run-contract",
                role=role,
                schema_version=schema,
                payload_json=raw,
                payload_hash=hashlib.sha256(raw.encode()).hexdigest(),
                created_at=NOW,
            )
        )
    session.flush()


def test_real_sqlite_new_task_permit_freeze_read_and_no_upgrade(
    writer_fixture, installation_fixture
):
    configure(installation_fixture)
    with writer_fixture.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with old.graph_recipe_task_creation_scope(session, "run-contract"):
            add_originals(session)
            frozen = recipe.freeze_graph_recipe_selection_v3_in_session(
                session, run_id="run-contract", recorded_at=NOW, quality_context=baseline()
            )
            assert (
                recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract") == frozen
            )
        assert (
            recipe.freeze_graph_recipe_selection_v3_in_session(
                session, run_id="run-contract", recorded_at=NOW, quality_context=baseline()
            )
            == frozen
        )
        with pytest.raises(ValueError, match="original_conflict"):
            recipe.freeze_graph_recipe_selection_v3_in_session(
                session,
                run_id="run-contract",
                recorded_at=NOW + timedelta(seconds=1),
                quality_context=baseline(),
            )
        with pytest.raises(ValueError, match="existing_task_not_upgradeable"):
            with old.graph_recipe_task_creation_scope(session, "run-contract"):
                pytest.fail("Existing Task must not obtain a new creation permit")


def test_swap_cannot_alias_spot_and_existing_task_cannot_be_retrofitted(
    writer_fixture, installation_fixture
):
    configure(installation_fixture)
    with writer_fixture.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with old.graph_recipe_task_creation_scope(session, "run-contract"):
            add_originals(session, instrument="BTC-USDT-SWAP")
            with pytest.raises(ValueError, match="original_scope_mismatch"):
                recipe.freeze_graph_recipe_selection_v3_in_session(
                    session, run_id="run-contract", recorded_at=NOW, quality_context=baseline()
                )
        with pytest.raises(ValueError, match="new_task_scope_required"):
            recipe.freeze_graph_recipe_selection_v3_in_session(
                session, run_id="run-contract", recorded_at=NOW, quality_context=baseline()
            )


def test_read_rejects_original_row_damage(writer_fixture, installation_fixture):
    configure(installation_fixture)
    with writer_fixture.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with old.graph_recipe_task_creation_scope(session, "run-contract"):
            add_originals(session)
            recipe.freeze_graph_recipe_selection_v3_in_session(
                session, run_id="run-contract", recorded_at=NOW, quality_context=baseline()
            )
        task = session.get(RunRoleOutputRecord, old.TASK_ROLE)
        task.payload_json += " "
        with pytest.raises(ValueError, match="original_row_invalid"):
            recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")


def test_v2_registration_source_bytes_remain_unchanged():
    saved = json.loads(
        (
            recipe._ROOT
            / "artifacts/market-risk-implementation-20261007/B2"
            / "recipe-v2-registration-baseline.json"
        ).read_text()
    )
    raw = (recipe._ROOT / saved["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == saved["sha256"]
    assert old.RECIPE_VERSION_V2 == "research-graph-projection.v2"
    assert old.SELECTION_SCHEMA == "research-graph-recipe-selection.v2"
