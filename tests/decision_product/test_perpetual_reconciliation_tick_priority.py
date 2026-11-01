"""A slow/failed closed-account source cannot precede live execution protection."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.parametrize("failure", [ValueError("perp_funding_source_temporarily_unavailable"), httpx.ReadTimeout("test source timeout"), OSError("test source unavailable"), TimeoutError()])
def test_closed_funding_failure_backs_off_after_protection_and_pending_execution(monkeypatch, failure):
    from packages.kernel.decision_hub_kernel.application.action_execution import (
        ActionExecutionService,
    )
    from packages.kernel.decision_hub_kernel.application.action_execution_observer import (
        ExecutionObservationService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_quote_sampling import (
        PerpetualQuoteSamplingAdapter,
    )

    events = []
    expired = [False]

    def checkpoint():
        if expired[0]:
            raise TimeoutError("tick deadline elapsed")

    class Session:
        def scalars(self, statement):
            sql = str(statement)
            if "FROM action_execution_intents" in sql:
                values = ["current-intent"]
            elif "active IS false" in sql:
                values = ["closed-account"]
            else:
                values = ["protected-account"]
            return SimpleNamespace(all=lambda: values)

    @contextmanager
    def session():
        yield Session()

    def funding(account, **_):
        assert account == "closed-account"
        events.append("funding")
        expired[0] = isinstance(failure, TimeoutError)
        raise failure

    def defer(account, reason, **options):
        if options.get("checkpoint"):
            options["checkpoint"]()
        assert account == "closed-account"
        assert reason == "perp_funding_source_temporarily_unavailable"
        events.append("backoff")
        return {"status": "retry_scheduled"}

    monkeypatch.setattr(PerpetualQuoteSamplingAdapter, "process_due", lambda *_a, **_k: None)
    monkeypatch.setattr(ExecutionObservationService, "process_due", lambda *_a, **_k: {"errors": [], "observed": 0})
    service = SimpleNamespace(
        enabled=True,
        database=SimpleNamespace(session=session),
        perpetual_execution=SimpleNamespace(funding_history=object(), reconcile_closed_episode=funding, defer_closed_episode=defer),
        _now=lambda: datetime(2026, 10, 19, tzinfo=UTC),
        observe_episode=lambda *_a, **_k: events.append("protection"),
        execute_intent=lambda *_a, **_k: events.append("intent") or {"receipt": "accepted"},
    )

    result = ActionExecutionService.process_due(service, checkpoint=checkpoint)

    assert events == ["protection", "intent", "funding", "backoff"]
    assert result["processed"] == 1 and result["funding_reconciled"] == 0
    assert result["errors"] == [{"portfolio_id": "closed-account", "reason": "perp_funding_source_temporarily_unavailable"}]
