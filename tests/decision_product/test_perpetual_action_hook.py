"""Actual pre-Task admission is discoverable after a failed Run, without model retries."""

from datetime import timedelta

from packages.kernel.decision_hub_kernel.application.action_research_integration import (
    ActionResearchIntegration,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, freeze
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_real_perpetual_admission_is_recovered_without_a_task_or_legacy_registration(binding_case):
    case = binding_case
    service, _ref = freeze(case)
    with case['db'].session() as session:
        admission = service.read_admission_in_session(session, case['run_id'])
        run = session.get(RunRecord, case['run_id'])
        run.status, run.error_code, run.finished_at = 'failed', 'synthetic_pre_task_failure', AT + timedelta(seconds=1)
    hook = ActionResearchIntegration(case['db'], clock=lambda: AT + timedelta(seconds=2))
    result = hook.reconcile_missed_hooks(max_candidates=3)
    assert len(result) == 1, 'native V2 admission is invisible to the existing hook recovery query'
    assert result[0]['status'] == 'missed'
    assert result[0]['reason'] == 'research_run_failed'
    assert result[0]['horizon'] == '4h' and result[0]['run_id'] == case['run_id']
    with case['db'].session() as session:
        assert ActionRepository(session).get_opportunity(admission['opportunity_id']).status == 'missed'
    assert hook.reconcile_missed_hooks(max_candidates=3) == ()
