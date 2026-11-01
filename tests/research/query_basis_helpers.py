from packages.contracts_py.decision_hub_contracts import DshSessionPrompt
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord
from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import FakeHostClient, _identity


def save_control_request(database, request, now, *, tool_calls=0, full_task=True):
    """Persist the same typed control request as the real submission boundary."""
    if full_task:
        from packages.kernel.decision_hub_kernel.application.research_task import (
            ResearchTaskService,
        )

        ResearchTaskService(database, clock=lambda: now).freeze(request)
    links = DshSessionLinkService(database, clock=lambda: now)
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    submission = runtime._submission(request)
    links.reserve(submission, _identity())
    with database.session() as session:
        session.get(DshSessionLinkRecord, request.run_id).tool_calls_started = tool_calls
    links.store_prompt(DshSessionPrompt(
        schema_version="dsh-session-prompt.v1", run_id=request.run_id, generation=1,
        dsh_session_id=submission.deterministic_session_id,
        request_id=submission.deterministic_request_id, request_hash=submission.request_hash,
        prompt=build_trusted_web_research_prompt(request),
    ))
