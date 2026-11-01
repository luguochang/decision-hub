"""Server originals for the DSH generate-options boundary, not model comprehension."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderFinishSubmit,
    ResearchProviderInputSubmit,
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchNativeObservationContext,
)

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Original(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderInputOriginal(Original):
    schema_version: Literal["research-provider-input.v1"]
    submit: ResearchProviderInputSubmit
    native_context: ResearchNativeObservationContext
    source_refs: tuple[ObservedExecutionRef, ...]
    prompt_request_id: str
    prompt_hash: Hash
    model_binding_ref: ObservedExecutionRef
    known_at: AwareDatetime
    proof_scope: Literal["dsh_llm_generate_options"] = "dsh_llm_generate_options"
    producer_authenticated: Literal[True] = True
    model_understanding_verified: Literal[False] = False
    financial_qualified: Literal[False] = False


class ProviderToolEmissionOriginal(Original):
    schema_version: Literal["research-provider-tool-emission.v1"]
    submit: ResearchProviderToolEmissionSubmit
    candidate_hash: Hash | None
    known_at: AwareDatetime


class ProviderFinishOriginal(Original):
    schema_version: Literal["research-provider-finish.v1"]
    submit: ResearchProviderFinishSubmit
    input_tokens: Annotated[int, Field(strict=True, ge=0)] | None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None
    total_tokens: Annotated[int, Field(strict=True, ge=0)] | None
    cost_usd: None = None
    usage_scope: Literal["dispatch_actual_usage_chunk_or_unknown"] = (
        "dispatch_actual_usage_chunk_or_unknown"
    )
    known_at: AwareDatetime


class ContextBodyCoverage(Original):
    evidence_ref: str
    body_hash: Hash
    source_kind: Literal["initial_prompt", "root_tool_result", "root_child_summary", "unavailable"]
    original_ref: ObservedExecutionRef | None
    input_loci: tuple[str, ...]
    covered: bool
    reason_code: str | None


class ResearchContextDelivery(Original):
    schema_version: Literal["research-context-delivery.v1"]
    run_id: str
    generation: int
    root_session_id: str
    selected_call_id: str | None
    selected_candidate_hash: Hash | None
    mapped_ref: ObservedExecutionRef
    transport_ref: ObservedExecutionRef
    provider_input_ref: ObservedExecutionRef | None
    provider_emission_ref: ObservedExecutionRef | None
    provider_finish_ref: ObservedExecutionRef | None
    selection_manifest_hash: Hash | None
    items: tuple[ContextBodyCoverage, ...]
    required_refs: tuple[str, ...]
    context_fidelity_verified: bool
    root_synthesis_input_verified: bool
    missing_codes: tuple[str, ...]
    proof_scope: Literal["dsh_llm_generate_options"] = "dsh_llm_generate_options"
    model_understanding_verified: Literal[False] = False
    financial_qualified: Literal[False] = False


class ContextBodyCoverageV2(ContextBodyCoverage):
    source_kind: Literal["initial_prompt", "root_tool_result", "root_child_summary", "server_baseline", "unavailable"]  # type: ignore[assignment]


class ResearchContextDeliveryV2(ResearchContextDelivery):
    schema_version: Literal["research-context-delivery.v2"]  # type: ignore[assignment]
    items: tuple[ContextBodyCoverageV2, ...]  # type: ignore[assignment]


Counter = Annotated[int, Field(strict=True, ge=0)]


class ProviderDispatchUsageV1(Original):
    generation: Annotated[int, Field(strict=True, ge=1)]
    root_session_id: str
    execution_session_id: str
    parent_session_id: str | None
    dispatch_id: str
    provider: str
    model: str
    input_ref: ObservedExecutionRef
    finish_ref: ObservedExecutionRef | None
    finish_status: Literal["completed", "interrupted", "cancelled", "error"] | None
    usage_status: Literal["known", "unknown"]
    input_tokens: Counter | None
    output_tokens: Counter | None
    total_tokens: Counter | None
    missing_codes: tuple[str, ...]

    @model_validator(mode="after")
    def counters(self):
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.usage_status == "known":
            if (
                any(value is None for value in values)
                or self.finish_ref is None
                or self.finish_status != "completed"
                or self.missing_codes
            ):
                raise ValueError("research_usage_known_dispatch_incomplete")
            assert self.input_tokens is not None and self.output_tokens is not None
            assert self.total_tokens is not None
            if self.total_tokens < self.input_tokens + self.output_tokens:
                raise ValueError("research_usage_dispatch_counter_invalid")
        elif any(value is not None for value in values) or not self.missing_codes:
            raise ValueError("research_usage_unknown_dispatch_counter_invalid")
        return self


class ProviderConversationUsageCensusV1(Original):
    """Authenticated conversation receipts, excluding compaction/title and fees."""

    schema_version: Literal["research-provider-conversation-usage.v1"]
    run_id: str
    as_of: AwareDatetime
    scope: Literal["conversation_only"] = "conversation_only"
    excluded_purposes: tuple[Literal["compaction"], Literal["title"]] = ("compaction", "title")
    coverage_status: Literal["complete", "incomplete"]
    token_status: Literal["known", "unknown"]
    cost_status: Literal["unknown"] = "unknown"
    expected_dispatches: Counter | None
    observed_dispatches: Counter
    root_dispatches: Counter
    child_dispatches: Counter
    covered_dispatches: Counter
    input_tokens: Counter | None
    output_tokens: Counter | None
    total_tokens: Counter | None
    cost_usd: None = None
    missing_codes: tuple[str, ...]
    missing_sessions: tuple[str, ...]
    dispatches: tuple[ProviderDispatchUsageV1, ...]
    source_refs: tuple[ObservedExecutionRef, ...]
    census_hash: Hash
    proof_scope: Literal["dsh_llm_generate_options"] = "dsh_llm_generate_options"
    model_understanding_verified: Literal[False] = False
    budget_qualified: Literal[False] = False
    financial_qualified: Literal[False] = False

    @model_validator(mode="after")
    def census(self):
        import hashlib
        import json

        keys = {
            (item.generation, item.root_session_id, item.execution_session_id, item.dispatch_id)
            for item in self.dispatches
        }
        proofs = {ref.output_id: ref for ref in self.source_refs}
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if (
            len(keys) != len(self.dispatches)
            or len({item.input_ref.output_id for item in self.dispatches}) != len(keys)
            or len(proofs) != len(self.source_refs)
            or self.observed_dispatches != len(keys)
            or self.root_dispatches + self.child_dispatches != len(keys)
            or self.root_dispatches
            != sum(item.execution_session_id == item.root_session_id for item in self.dispatches)
            or self.covered_dispatches
            != sum(item.usage_status == "known" for item in self.dispatches)
            or any(
                proofs.get(ref.output_id) != ref
                for item in self.dispatches
                for ref in (item.input_ref, item.finish_ref)
                if ref is not None
            )
            or any(ref.recorded_at > self.as_of for ref in self.source_refs)
        ):
            raise ValueError("research_usage_census_identity_invalid")
        if self.coverage_status == "complete":
            if (
                self.expected_dispatches != self.observed_dispatches
                or not self.observed_dispatches
                or self.covered_dispatches != self.observed_dispatches
                or self.missing_sessions
                or self.missing_codes != ("provider_pricing_authority_unavailable",)
                or self.token_status != "known"
            ):
                raise ValueError("research_usage_complete_census_invalid")
        elif self.token_status != "unknown" or not self.missing_codes:
            raise ValueError("research_usage_incomplete_census_invalid")
        if self.token_status == "known":
            if self.coverage_status != "complete" or any(value is None for value in values):
                raise ValueError("research_usage_known_total_incomplete")
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                if getattr(self, field) != sum(getattr(item, field) for item in self.dispatches):
                    raise ValueError("research_usage_total_counter_invalid")
        elif any(value is not None for value in values):
            raise ValueError("research_usage_unknown_total_invalid")
        encoded = json.dumps(
            self.model_dump(mode="json", exclude={"census_hash"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != self.census_hash:
            raise ValueError("research_usage_census_hash_invalid")
        return self
