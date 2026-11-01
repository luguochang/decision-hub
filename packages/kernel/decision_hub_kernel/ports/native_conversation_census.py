"""Internal authenticated native denominator; no billing/model authority."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .research_observation import ObservedExecutionRef, ResearchNativeObservationContext

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Id = Annotated[str, Field(min_length=1, max_length=256)]
Count = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
Code = Annotated[str, Field(pattern=r"^native_[a-z_]{1,100}$")]


class _Original(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NativeSessionCensusV1(_Original):
    session_id: Id
    parent_session_id: Id | None
    lifecycle_id: Id
    birth_status: Literal["observed", "missing", "remote_unavailable"]
    first_live_seq: Count | None
    native_first_seq: Count | None
    native_last_seq: Count | None
    settled: Annotated[bool, Field(strict=True)]
    terminal_seq: Count | None
    terminal_kind: Id | None
    capture_status: Literal["complete", "unknown"]
    missing_codes: tuple[Code, ...] = Field(max_length=100)
    events_json: str | None = Field(max_length=8_388_608)
    events_sha256: Hash | None


class NativeConversationCensusV1(_Original):
    schema_version: Literal["decision-hub-native-conversation-census.v1"]
    registry_epoch_id: Id
    run_id: Id
    generation: Annotated[int, Field(strict=True, ge=1)]
    root_session_id: Id
    request_hash: Hash
    plugin_build_hash: Hash
    registry_status: Literal["complete", "unknown"]
    observer_installed_before_root_birth: Annotated[bool, Field(strict=True)]
    registered_session_ids: tuple[Id, ...] = Field(max_length=64)
    missing_codes: tuple[Code, ...] = Field(max_length=100)
    sessions: tuple[NativeSessionCensusV1, ...] = Field(max_length=64)
    proof_scope: Literal["official_session_native_events"]
    model_understanding_verified: Literal[False]
    budget_qualified: Literal[False]
    financial_qualified: Literal[False]
    census_sha256: Hash

    @property
    def dispatch_id(self) -> str:
        # Diagnostic correlation only, never a provider-dispatch denominator.
        return self.registry_epoch_id


class NativeConversationCensusOriginal(_Original):
    schema_version: Literal["research-native-conversation-census.v1"]
    census: NativeConversationCensusV1
    native_context: ResearchNativeObservationContext
    source_refs: tuple[ObservedExecutionRef, ...]
    model_binding_refs: tuple[ObservedExecutionRef, ...]
    known_at: AwareDatetime
    producer_authenticated: Literal[True] = True
    proof_scope: Literal["authenticated_host_lifecycle_native_snapshots"] = (
        "authenticated_host_lifecycle_native_snapshots"
    )
    model_understanding_verified: Literal[False] = False
    budget_qualified: Literal[False] = False
    financial_qualified: Literal[False] = False


class NativeConversationCensusRecorded(_Original):
    schema_version: Literal["research-native-conversation-census-recorded.v1"] = (
        "research-native-conversation-census-recorded.v1"
    )
    run_id: str
    generation: int
    census_sha256: Hash
    reference: ObservedExecutionRef
    producer_authenticated: Literal[True] = True
    proof_scope: Literal["authenticated_host_lifecycle_native_snapshots"] = (
        "authenticated_host_lifecycle_native_snapshots"
    )
    model_understanding_verified: Literal[False] = False
    budget_qualified: Literal[False] = False
    financial_qualified: Literal[False] = False
