"""The recorded internal deadline is public; arbitrary exception messages are not."""
from apps.hub_worker.research import _error_provenance


def test_exact_original_deadline_is_retained_without_turning_it_into_a_retry():
    value = _error_provenance(PermissionError("original_preparation_deadline_elapsed"))
    assert value.cause_code == "original_preparation_deadline_elapsed"
    assert value.error_code == "research_worker_failed"
    assert value.retryable is False


def test_other_private_exception_messages_never_enter_public_cause():
    for error in (PermissionError("private request token=private-value"),
                  ValueError("original_preparation_deadline_elapsed")):
        value = _error_provenance(error)
        assert value.cause_code == type(error).__name__
        assert "private-value" not in value.model_dump_json()
