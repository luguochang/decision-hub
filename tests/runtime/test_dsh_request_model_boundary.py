"""Recorded nh06 model compatibility only; no DB, Host, issuer or live model."""
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    ResearchContextManifest,
    ResearchExecutionMethodReceiptV3,
    ResearchExecutionRequestReceiptV3,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.runtime_adapters.dsh_runtime.client import DshSdkRun
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run

FIXTURES = Path(__file__).resolve().parent / "fixtures/dsh_request_model_boundary"


@dataclass(frozen=True)
class Recorded:
    execution: ResearchExecutionRequestReceiptV3
    method: ResearchExecutionMethodReceiptV3
    host: DshSessionResult
    sdk: DshSdkRun


@pytest.fixture
def recorded() -> Recorded:
    execution = ResearchExecutionRequestReceiptV3.model_validate_json(
        (FIXTURES / "execution-request.json").read_bytes()
    )
    method = ResearchExecutionMethodReceiptV3.model_validate_json(
        (FIXTURES / "method.json").read_bytes()
    )
    host = DshSessionResult.model_validate_json((FIXTURES / "host-result.json").read_bytes())
    return Recorded(execution, method, host, _sdk_run(host))


def mapped(case: Recorded, request: BaseModel) -> ResearchSessionResult:
    public = ResearchSessionRequest.model_validate(request.model_dump(mode="json"))
    traces = map_notifications(
        case.sdk.notifications, run_id=public.run_id,
        research_session_id=case.sdk.session_id, received_at=case.host.finished_at,
        request=public,
    )
    assert len(traces) == 34
    return map_session_result_with_witness(
        case.sdk, request, traces, runtime_version=case.method.definition.runtime_version,
        profile_ref=case.method.definition.profile_ref, started_at=case.host.started_at,
        finished_at=case.host.finished_at, accepted_synthesis_only=True,
    ).result


def test_actual_generated_request_maps_with_complete_manifest(recorded: Recorded) -> None:
    request = recorded.execution.request
    manifest = request.context_manifest
    assert manifest is not None
    assert type(manifest) is not ResearchContextManifest
    before = request.model_dump_json()
    result = mapped(recorded, request)
    assert result.context_manifest is not None
    assert type(result.context_manifest) is ResearchContextManifest
    assert result.context_manifest.model_dump(mode="json") == manifest.model_dump(mode="json")
    assert request.run_id == recorded.host.run_id
    assert result.request_id == request.request_id
    assert request.model_dump_json() == before


def test_public_and_generated_requests_preserve_identical_result_wire(recorded: Recorded) -> None:
    request = recorded.execution.request
    public = ResearchSessionRequest.model_validate(request.model_dump(mode="json"))
    assert public.model_dump(mode="json") == request.model_dump(mode="json")
    assert mapped(recorded, request).model_dump(mode="json") == mapped(
        recorded, public
    ).model_dump(mode="json")


def test_bad_manifest_is_rejected_without_dropping_it(recorded: Recorded) -> None:
    request = recorded.execution.request
    assert request.context_manifest is not None
    bad = request.model_copy(update={"context_manifest": request.context_manifest.model_copy(
        update={"revision": "invalid-integer"}
    )})
    with pytest.raises(ValidationError) as failure:
        mapped(recorded, bad)
    assert any("revision" in item["loc"] for item in failure.value.errors())


def test_legacy_none_manifest_remains_none(recorded: Recorded) -> None:
    request = recorded.execution.request.model_copy(update={"context_manifest": None})
    assert mapped(recorded, request).context_manifest is None
