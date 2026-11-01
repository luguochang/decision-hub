"""Bundle-only reuse on real SQLite and actual prepared Factory originals."""
import hashlib
import json
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import event

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application import research_binding as binding
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_recipe_read_scope import (
    recipe_original_read_scope,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.decision.research_policy_registry import (
    GATE_RULE_IDS,
    NONBLOCKING_RULE_IDS,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _prepared_pack(tmp_path):
    # Same prepared policy fixture recipe as test_full_method_v2_bundle, without
    # importing its untracked test dependencies or another checkout's products.
    root = tmp_path / "prepared-pack"
    shutil.copytree(ROOT / "packs/crypto_macro", root)
    schema = json.loads((ROOT / "contracts/schemas/research_policy.schema.yaml").read_text("utf-8"))
    rules = []
    for branch in schema["$defs"]["research_policy_rule"]["oneOf"]:
        spec = schema["$defs"][branch["$ref"].rsplit("/", 1)[1]]["properties"]
        identity = spec["rule_id"]["const"]
        rules.append(dict(
            rule_id=identity, rule_version="1.0.0", category=spec["category"]["const"],
            phase="commit", applies_to=["event", "current_snapshot", "historical_review"],
            blocking_for=[] if identity in NONBLOCKING_RULE_IDS else
                ["execution_permission"] if identity == "execution-permission" else
                ["research_completeness", "research_publication"],
            params={key: item.get("const", 0.65)
                    for key, item in spec["params"]["properties"].items()},
        ))
    gate = dict(
        schema_version="research-gate-policy.v2", policy_id="test-gate", policy_version="2.0.0",
        horizon_contract_version="research-horizon-contract.v1",
        kernel_safeguards={key: item.get("const", 0.65) for key, item in
                           schema["$defs"]["research_kernel_safeguards"]["properties"].items()},
        rules=[rule for rule in rules if rule["rule_id"] in GATE_RULE_IDS],
    )
    rubric = dict(
        schema_version="research-rubric-policy.v2", rubric_id="test-rubric", rubric_version="2.0.0",
        checks=[rule for rule in rules if rule["rule_id"] not in GATE_RULE_IDS],
        prospective_metrics=["brier_score"], operational_metrics=["estimated_cost_usd"],
        promotion_policy="owner_review_required_replay_not_profitability",
    )
    manifest = yaml.safe_load((root / "pack.yaml").read_text("utf-8"))
    gate_ref, rubric_ref = "gates/prepared.yaml", "evaluations/prepared.yaml"
    (root / gate_ref).write_text(yaml.safe_dump(gate), encoding="utf-8")
    (root / rubric_ref).write_text(yaml.safe_dump(rubric), encoding="utf-8")
    refs = []
    for ref in manifest["role_profile_refs"]:
        profile = yaml.safe_load((root / ref).read_text("utf-8"))
        profile["evaluation_policy_ref"] = f"../{rubric_ref}"
        new_ref = f"profiles/prepared-{Path(ref).name}"
        (root / new_ref).write_text(yaml.safe_dump(profile), encoding="utf-8")
        refs.append(new_ref)
    manifest.update(gate_policy_ref=gate_ref, evaluation_policy_ref=rubric_ref, role_profile_refs=refs)
    (root / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root, manifest


@pytest.fixture
def bundle_case(tmp_path, monkeypatch):
    # Everything executed below imports this checkout; main's interpreter only
    # supplies installed dependencies. No production DB or Provider is involved.
    assert Path(binding.__file__).resolve().is_relative_to(ROOT)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'bundle.sqlite3'}")
    database.create_all()
    event_id, _, admitted = AdmissionService(database, clock=lambda: NOW).admit(ObservationCreate(
        text="The official event confirms elevated inflation risks.", source_id="bundle-fixture",
        source_type=SourceType.transcript, observed_at=NOW - timedelta(seconds=2),
        published_at=NOW - timedelta(seconds=3), language="en", event_hint="central_bank_speech",
    ))
    assert admitted
    run_id, created = RunService(database).create(event_id, strategy_version="research.v1")
    assert created
    pack_root, manifest = _prepared_pack(tmp_path)
    factory = ResearchRequestFactory(
        database, pack_root=pack_root, execution_mode="replay",
        allowed_capabilities=("replay.research", "document.readback", "web.search"),
        prepared_policy_refs=binding.PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
    )
    factory.build(event_id, run_id)
    calls = []
    original = binding._validate_current_source

    def authenticated(session, run, task):
        calls.append(run.run_id)
        return original(session, run, task)

    monkeypatch.setattr(binding, "_validate_current_source", authenticated)
    try:
        yield SimpleNamespace(database=database, run_id=run_id, calls=calls, pack_root=pack_root)
    finally:
        database.engine.dispose()


@contextmanager
def _snapshot(case):
    with case.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        with recipe_original_read_scope(session):
            yield session


def test_same_snapshot_authenticates_once_and_clones_all_three_receipts(bundle_case):
    case = bundle_case
    with _snapshot(case) as session:
        first = binding.read_research_bundle_in_session(session, case.run_id)
        second = binding.read_research_bundle_in_session(session, case.run_id)
        assert isinstance(first, binding.FrozenResearchBundle)
        for name in ("pack", "task", "compiled"):
            assert getattr(first, name) is not getattr(second, name)
            assert getattr(first, name).model_dump_json() == getattr(second, name).model_dump_json()
        original_excerpt = second.task.request.input_evidence[0].excerpt
        first.task.request.input_evidence[0].excerpt = "mutated returned clone"
        third = binding.read_research_bundle_in_session(session, case.run_id)
        assert third.task.request.input_evidence[0].excerpt == original_excerpt
        assert len(case.calls) == 1
    assert len(case.calls) == 2  # Full original reader again at successful scope exit.


def test_warm_broad_assessment_cannot_bypass_narrow_assessment(bundle_case):
    with _snapshot(bundle_case) as session:
        first = binding.read_research_bundle_in_session(session, bundle_case.run_id)
        binding.read_research_bundle_in_session(
            session, bundle_case.run_id, assessed_at=first.recorded_at + timedelta(seconds=1),
        )
        with pytest.raises(binding.ResearchBindingError, match="receipt_time_invalid"):
            binding.read_research_bundle_in_session(
                session, bundle_case.run_id, assessed_at=first.recorded_at - timedelta(seconds=1),
            )


@pytest.mark.parametrize("dependency", ["raw", "source"])
def test_scope_exit_reauthenticates_changed_external_dependency(bundle_case, tmp_path, monkeypatch,
                                                               dependency):
    # Keep the complete real bundle reader and SQL originals. A controlled
    # external dependency stands in for a selected CPI/archive reader; raw uses
    # the actual archive's content-addressed byte authentication.
    archive = RawBodyArchive(tmp_path / "archive")
    digest, ref = archive.put(b"authenticated raw original")
    source = tmp_path / "reader-source.py"
    source.write_bytes(b"authenticated source identity")
    expected = hashlib.sha256(source.read_bytes()).digest()
    original = binding._validate_current_source

    def with_external_dependency(session, run, task):
        original(session, run, task)
        archive.read(ref)
        if hashlib.sha256(source.read_bytes()).digest() != expected:
            raise ValueError("selected_source_identity_changed")

    monkeypatch.setattr(binding, "_validate_current_source", with_external_dependency)
    with pytest.raises(binding.ResearchBindingError, match="receipt_invalid"):
        with _snapshot(bundle_case) as session:
            binding.read_research_bundle_in_session(session, bundle_case.run_id)
            binding.read_research_bundle_in_session(session, bundle_case.run_id)
            if dependency == "raw":
                (archive.root / digest[:2] / digest).write_bytes(b"tampered raw original")
            else:
                source.write_bytes(b"changed source identity")
    assert len(bundle_case.calls) == 2  # First real auth and failed exit real auth.


def test_new_snapshot_does_not_reuse_previous_authentication(bundle_case):
    case = bundle_case
    for expected in (1, 3):
        with _snapshot(case) as session:
            binding.read_research_bundle_in_session(session, case.run_id)
            binding.read_research_bundle_in_session(session, case.run_id)
            assert len(case.calls) == expected
    assert len(case.calls) == 4


@pytest.mark.parametrize("change", ["sql", "pending_orm", "transaction"])
def test_write_or_replaced_transaction_cannot_hit_readonly_scope(bundle_case, change):
    with pytest.raises(ValueError, match="read_scope_changed"):
        with _snapshot(bundle_case) as session:
            binding.read_research_bundle_in_session(session, bundle_case.run_id)
            if change == "sql":
                session.connection().exec_driver_sql(
                    "UPDATE runs SET status='cancelled' WHERE run_id=?", (bundle_case.run_id,),
                )
            elif change == "pending_orm":
                session.get(RunRecord, bundle_case.run_id).status = "cancelled"
            else:
                session.commit()
                session.connection().exec_driver_sql("BEGIN")
            binding.read_research_bundle_in_session(session, bundle_case.run_id)


def test_no_scope_keeps_original_authentication_on_every_call(bundle_case):
    with bundle_case.database.session() as session:
        binding.read_research_bundle_in_session(session, bundle_case.run_id)
        binding.read_research_bundle_in_session(session, bundle_case.run_id)
    assert len(bundle_case.calls) == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_none_and_legacy_results_keep_uncached_behavior(tmp_path, legacy):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'empty.sqlite3'}")
    database.create_all()
    run_id = "legacy" if legacy else "empty"
    with database.session() as session:
        session.add(RunRecord(run_id=run_id, event_id="event", status="running",
                              created_at=NOW, updated_at=NOW))
        if legacy:
            session.add(RunRoleOutputRecord(
                output_id="legacy-pack", run_id=run_id, role=binding.PACK_ROLE,
                schema_version="research-request-pack.v1", payload_json="{}", payload_hash="0" * 64,
                created_at=NOW,
            ))
    queries = []
    try:
        with database.session() as session:
            connection = session.connection()
            connection.exec_driver_sql("BEGIN")

            def count(_connection, _cursor, _statement, parameters, _context, _executemany):
                if parameters == (run_id, *binding.BUNDLE_ROLES):
                    queries.append(parameters)

            event.listen(connection, "before_cursor_execute", count)
            with recipe_original_read_scope(session):
                first = binding.read_research_bundle_in_session(session, run_id)
                second = binding.read_research_bundle_in_session(session, run_id)
                if legacy:
                    assert isinstance(first, binding.LegacyResearchBundle)
                    assert isinstance(second, binding.LegacyResearchBundle)
                else:
                    assert first is second is None
                assert len(queries) == 2
            assert len(queries) == 2  # Neither result creates an exit verification entry.
    finally:
        database.engine.dispose()


def test_bundle_reuse_preserves_real_preparation_writer_readset(bundle_case):
    def read(session):
        with recipe_original_read_scope(session):
            binding.read_research_bundle_in_session(session, bundle_case.run_id)
            return binding.read_research_bundle_in_session(session, bundle_case.run_id).task.request_hash

    saved = preparation.prepare_originals(bundle_case.database, read)
    assert len(bundle_case.calls) == 2 and saved.rows and saved.queries
    with bundle_case.database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=bundle_case.run_id, role=binding.TASK_ROLE,
        ).one()
        row.payload_json = "{}"
    with bundle_case.database.session() as session, pytest.raises(ValueError, match="readset_changed"):
        preparation.begin_immediate(session)
        saved.verify(session)
