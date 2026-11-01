"""Actual SQLite account/protocol originals; no execution or enabled route."""

from datetime import timedelta

import pytest

from tests.decision_product.test_okx_perpetual_original import NOW
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_paper import creation, partition, service
from tests.decision_product.test_perpetual_paper import runtime as runtime


def test_registered_protocol_reopens_same_registry_and_v1_reader_rejects(runtime):
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
        ExecutionRepository,
    )

    service(runtime).create_account("alice", creation(runtime))
    db, _, source, _ = runtime
    protocols = PerpetualPaperProtocolService(db, source, clock=lambda: NOW)
    value = protocols.register(
        owner_id="alice",
        account_id="practice",
        trial_id="paper-design",
        start_at=NOW + timedelta(seconds=1),
        end_at=NOW + timedelta(hours=4),
    )
    assert value.execution_authority is False and value.execution_mode == "manual_after_visible"
    assert (
        value.reaction_delay_seconds,
        value.quote_max_age_seconds,
        value.entry_wait_seconds,
        value.top_size_participation,
    ) == (1, 5, 30, "0.1")
    assert value.fee_rate == "0.0005" and value.slippage_bps == "2"
    assert value.size_policy_ref is None and value.exit_policy_ref is None
    assert (
        protocols.register(
            owner_id="alice",
            account_id="practice",
            trial_id="paper-design",
            start_at=NOW + timedelta(seconds=1),
            end_at=NOW + timedelta(hours=4),
        )
        == value
    )
    with db.session() as session:
        assert protocols.read_in_session(session, value.reference) == value
        assert ExecutionRepository(session).perpetual_protocol(value.id) == value
        with pytest.raises(ValueError, match="protocol_version"):
            ExecutionRepository(session).protocol(value.id)
    with db.session() as session:
        assert service(runtime).account_reader(session, "alice", partition())[0].equity == "2500"


def test_protocol_owner_and_immutable_account_mode_are_authenticated(runtime):
    from sqlalchemy.exc import IntegrityError

    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
        ExecutionProtocolRecord,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
        canonical,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.perpetual_execution_protocol import protocol_id

    service(runtime).create_account("alice", creation(runtime))
    db, _, source, _ = runtime
    protocols = PerpetualPaperProtocolService(db, source, clock=lambda: NOW)
    with pytest.raises(ValueError, match="account_missing"):
        protocols.register(
            owner_id="bob",
            account_id="practice",
            trial_id="same",
            start_at=NOW,
            end_at=NOW + timedelta(hours=1),
        )
    value = protocols.register(
        owner_id="alice",
        account_id="practice",
        trial_id="same",
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
    )
    body = value.model_dump(mode="json")
    body["execution_mode"] = "server_auto_paper"
    body["payload_hash"] = canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
    with pytest.raises(IntegrityError, match="original_immutable"), db.session() as session:
        row = session.get(ExecutionProtocolRecord, value.id)
        row.payload_json, row.payload_hash = canonical(body), body["payload_hash"]
    # A separate low-level inserted row still grants no account/source authority.
    body["trial_id"] = "different"
    body["id"] = protocol_id("alice", "practice", "different", value.T0, value.T1)
    body["payload_hash"] = canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
    with db.session() as session:
        session.add(
            ExecutionProtocolRecord(
                protocol_id=body["id"],
                trial_id="different",
                registered_at=NOW,
                start_at=value.T0,
                end_at=value.T1,
                payload_json=canonical(body),
                payload_hash=body["payload_hash"],
            )
        )
    changed = value.reference.model_copy(update={"ref": body["id"], "hash": body["payload_hash"]})
    with db.session() as session, pytest.raises(ValueError, match="account_binding"):
        protocols.read_in_session(session, changed)


def test_mixed_registry_keeps_legacy_demand_and_perpetual_registration_has_none(runtime):
    from packages.contracts_py.decision_hub_contracts.generated.action_execution_v1 import (
        ActionExecutionProtocolV1,
    )
    from packages.kernel.decision_hub_kernel.application.action_execution_demand import (
        ExecutionQuoteDemandReader,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
        ExecutionRepository,
        seal,
    )
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
    from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope

    service(runtime).create_account("alice", creation(runtime))
    db, _, source, _ = runtime
    PerpetualPaperProtocolService(db, source, clock=lambda: NOW).register(
        owner_id="alice",
        account_id="practice",
        trial_id="perp-design-only",
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
    )
    reader = ExecutionQuoteDemandReader(db.session)
    assert reader.scopes(NOW) == ()
    # Existing valid V1 transport fixture only, not a real trading authorization.
    scope, digest = spot_scope("BTC-USDT"), "a" * 64
    body = dict(
        schema_version="action-execution-protocol.v1",
        id="legacy-protocol",
        trial_id="legacy-trial",
        registered_at=NOW,
        T0=NOW,
        T1=NOW + timedelta(hours=1),
        label_deadline=NOW + timedelta(hours=2),
        cohort="diagnostic",
        primary_horizon="30m",
        diagnostic_horizons=[],
        scopes=[scope.scope_id],
        methods=[dict(ref="fixture:method", hash=digest)],
        channels=["shadow_engine"],
        delays=[0],
        targets=[dict(horizon="30m", target_spec_ref="fixture:target", target_spec_hash=digest)],
        initial_equity="10000",
        unit="USDT",
        paper_quantity_step="0.0001",
    )
    for name in ("cost_policy", "size_policy", "calculation_manifest", "opening_rule"):
        body[name + "_ref"], body[name + "_hash"] = "fixture:" + name, digest
    legacy = seal(ActionExecutionProtocolV1, body)
    with db.session() as session:
        begin_immediate(session)
        ExecutionRepository(session).register_protocol(legacy, now=NOW)
    assert reader.scopes(NOW) == (scope,)
    with db.session() as session:
        assert ExecutionRepository(session).protocol(legacy.id) == legacy
