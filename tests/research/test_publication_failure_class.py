"""Closed producer diagnostics using a fake fenced lifecycle only."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application.research_publication_jobs48 import (
    PublicationBundleWorker, _publication_failure_class,
)


class PrivateNamedError(ValueError):
    pass


@pytest.mark.parametrize("kind", [MemoryError, ValueError, TypeError, PermissionError,
                                  TimeoutError, RuntimeError, OSError, Exception,
                                  PrivateNamedError])
def test_diagnostic_failure_retains_closed_producer_class(kind, caplog):
    original = kind("SECRET_SQL_COMMAND_TOKEN_LOCALS")
    claim = SimpleNamespace(run_id="fixture-run", request_id="fixture-request", generation=3)

    def diagnostic(*_args):
        raise ValueError("SECRET_DIAGNOSTIC_MESSAGE")

    def forbidden(*_args, **_kwargs):
        pytest.fail("unknown fence must not grant a failure write")

    worker = object.__new__(PublicationBundleWorker)
    worker.owner = "fixture-owner"
    worker.lifecycle = SimpleNamespace(
        claim_one=lambda **_kwargs: claim, diagnostic_scope=lambda _claim: nullcontext(),
        record_failure_diagnostic=diagnostic, preparer=SimpleNamespace(),
        archive=SimpleNamespace(fenced=lambda _run: True), fail_claim=forbidden,
    )

    def producer(_claim):
        raise original

    worker.producer = producer
    assert worker.process_one() is False
    expected = "unknown" if kind is PrivateNamedError else kind.__name__
    assert _publication_failure_class(original) == expected
    assert "type=ValueError producer_type=" + expected + " claim=" in caplog.text
    assert "SECRET" not in caplog.text
    assert "PrivateNamedError" not in caplog.text
