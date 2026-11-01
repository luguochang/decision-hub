from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from packages.query_views.research.service import _action_summary
from tests.evolution.test_research_worker import NOW, _result


@pytest.mark.parametrize("child_offset_minutes", [-1, 5])
def test_child_admission_time_cannot_replace_saved_report_review_suggestion(
    child_offset_minutes: int,
) -> None:
    # Private control values represent an earlier manual child or an admitted
    # scheduled child. Neither admission timestamp is a report recommendation.
    result = _result(SimpleNamespace(
        current_round=1, evidence_refs=[], request_id="review-time-control",
    ))
    original_json = result.model_dump_json()
    run = SimpleNamespace(
        run_id="review-time-control", status="research_only", failure=None,
        coverage=result.final_coverage, event_title="BTC review control",
    )
    summary = _action_summary(
        run, SimpleNamespace(gate_status="research_only"), result,
        result.evidence_candidates,
        scheduled_recheck_at=NOW + timedelta(minutes=child_offset_minutes),
    )

    assert summary.next_review_at == NOW + timedelta(minutes=10)
    assert summary.next_review_at == min(item.next_review_at for item in result.horizons)
    assert result.model_dump_json() == original_json
    assert summary.trade_authorized is False


def test_child_admission_without_report_cannot_invent_report_review_suggestion() -> None:
    summary = _action_summary(
        SimpleNamespace(
            run_id="unsaved-review-control", status="researching", failure=None,
            coverage=None, event_title="BTC review control",
        ),
        None, None, [], scheduled_recheck_at=NOW,
    )

    assert summary.next_review_at is None
    assert summary.artifact_status == "pending"
    assert summary.trade_authorized is False
