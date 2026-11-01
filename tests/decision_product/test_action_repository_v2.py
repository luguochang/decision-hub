"""Actual SQLite Action repository retains old originals and writes native V2."""

from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionPlanRevisionV2,
    DecisionOpportunityV2,
    DecisionSnapshotV2,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import (
    ActionRepository,
    ActionRepositoryError,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.action_plan import ActionPlanHead, Opportunity
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from tests.contracts.test_d2_candidate_contract import publication_revision, publication_snapshot


def seal(model, value, field):
    value[field] = canonical_hash({key: body for key, body in value.items() if key != field})
    return model.model_validate(value)


def test_actual_repository_writes_reads_and_rejects_substituted_v2_final_target(tmp_path, monkeypatch):
    monkeypatch.setenv('DECISION_HUB_DATA_DIR', str(tmp_path / 'data'))
    database = Database('sqlite:///' + (tmp_path / 'actions.sqlite3').as_posix())
    database.create_all()
    snapshot = seal(DecisionSnapshotV2, publication_snapshot(), 'snapshot_hash')
    revision_body = publication_revision()
    revision_body['target_spec_hash'] = revision_body['bindings']['final_target']['hash']
    revision = seal(ActionPlanRevisionV2, revision_body, 'payload_hash')
    opportunity = DecisionOpportunityV2.model_validate({
        'schema_version': 'decision-opportunity.v2', 'id': 'opportunity', 'key': 'perp:run:4h',
        'scope_id': snapshot.scope_id, 'horizon': '4h', 'kind': 'event', 'event_ref': 'event',
        'registered_at': '2026-01-01T00:00:00Z', 'time_start': '2026-01-01T00:00:00Z',
        'time_end': '2026-01-01T02:00:00Z',
        'target_spec_hash': revision.bindings.candidate_target.hash, 'recipe_hash': 'e' * 64,
        'cohort': 'diagnostic', 'status': 'open', 'reason': None,
    })
    head = ActionPlanHead.model_validate({
        'schema_version': 'decision-action-plan-head.v1', 'plan_id': revision.plan_id,
        'current_revision_id': revision.revision_id, 'state_version': 1, 'state': 'proposed',
        'monitor_state': 'paused', 'next_due': datetime(2026, 1, 1, 0, 31, tzinfo=UTC),
        'valid_until': revision.valid_until, 'enter_streak': 0, 'last_input_watermark': None,
        'lease_owner': None, 'lease_expires_at': None, 'lease_generation': 0,
    })
    try:
        with database.session() as session:
            begin_immediate(session)
            repository = ActionRepository(session)
            assert repository.register_opportunity(opportunity) == opportunity
            assert repository.append_snapshot(snapshot) == snapshot
            repository.create_plan(revision, head=head)
        with database.session() as session:
            repository = ActionRepository(session)
            assert repository.get_opportunity(opportunity.id) == opportunity
            assert repository.get_snapshot(snapshot.id) == snapshot
            assert repository.get_revision(revision.revision_id) == revision
            assert repository.get_head(revision.plan_id) == head
        changed = revision.model_dump(mode='json')
        changed.update(revision_id='forged', plan_id='forged-plan', target_spec_hash='f' * 64)
        changed['bindings']['final_target']['hash'] = 'f' * 64
        with pytest.raises(ActionRepositoryError, match='action_target_recipe_mismatch'):
            with database.session() as session:
                begin_immediate(session)
                ActionRepository(session).create_plan(
                    seal(ActionPlanRevisionV2, changed, 'payload_hash'),
                    head=head.model_copy(update={'plan_id': 'forged-plan', 'current_revision_id': 'forged'}),
                )
        with database.session() as session:
            assert ActionRepository(session).get_revision('forged') is None
    finally:
        database.engine.dispose()


def test_additive_migration_preserves_actual_old_perp_24h_without_guessing_v2(tmp_path, monkeypatch):
    monkeypatch.setenv('DECISION_HUB_DATA_DIR', str(tmp_path / 'data'))
    database = Database('sqlite:///' + (tmp_path / 'migration.sqlite3').as_posix())
    config = database._alembic_config()
    config.attributes['configure_logger'] = False
    try:
        command.upgrade(config, '0100_perpetual_paper')
        old = {'opportunity_id': 'old', 'key': 'old:perp:24h',
               'scope_id': 'crypto:perp:okx:BTC-USDT-SWAP:trade:1m', 'horizon': '24h',
               'kind': 'event', 'event_ref': None, 'recipe_hash': 'a' * 64,
               'target_hash': 'b' * 64, 'registered_at': '2026-01-01 00:00:00.000000',
               'start_at': '2026-01-01 00:00:00.000000', 'end_at': '2026-01-01 01:00:00.000000',
               'cohort': 'diagnostic', 'status': 'open', 'reason': None}
        with database.engine.begin() as connection:
            connection.execute(text('INSERT INTO decision_opportunities (' + ','.join(old) +
                                    ') VALUES (' + ','.join(':' + key for key in old) + ')'), old)
        database.initialize()
        with database.engine.connect() as connection:
            saved = dict(connection.execute(text('SELECT * FROM decision_opportunities WHERE opportunity_id=:id'), {'id': 'old'}).mappings().one())
            assert saved.pop('schema_version') == 'decision-opportunity.v1'
            assert saved == old
        with database.session() as session:
            read = ActionRepository(session).get_opportunity('old')
            assert isinstance(read, Opportunity) and read.horizon == '24h'
        # An empty V2 population can roll back this additive discriminator.
        command.downgrade(config, '0100_perpetual_paper')
        with database.engine.connect() as connection:
            assert dict(connection.execute(text('SELECT * FROM decision_opportunities WHERE opportunity_id=:id'), {'id': 'old'}).mappings().one()) == old
    finally:
        database.engine.dispose()
