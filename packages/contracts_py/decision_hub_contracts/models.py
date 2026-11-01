from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .generated import agentic_research as _agentic_research
from .generated import dsh_host_bridge as _dsh_host_bridge
from .generated import historical_data as _historical_data
from .generated import research_library as _research_library
from .generated import research_policy as _research_policy
from .generated import research_binding as _research_binding
from .generated import research_execution_binding as _research_execution_binding
from .generated import source_acquisition as _source_acquisition
from .generated.evolution_job import CandidateProposal as _CandidateProposal
from .generated.evolution_job import CapabilityOperation as _CapabilityOperation
from .generated.evolution_job import EvolutionJob as _EvolutionJob
from .generated.evolution_job import EvolutionJobCounts as _EvolutionJobCounts
from .generated.evolution_job import EvolutionJobCreate as _EvolutionJobCreate
from .generated.evolution_job import EvolutionPlanningContext as _EvolutionPlanningContext
from .generated.evolution_job import OperationsOverview as _OperationsOverview
from .generated.evolution_job import RuntimeMode as _RuntimeMode
from .generated.evolution_job import SearchEvidence as _SearchEvidence
from .generated.evolution_job import SearchQuery as _SearchQuery
from .generated.evolution_job import SearchResult as _SearchResult
from .generated.evolution_job import ServiceHeartbeat as _ServiceHeartbeat
from .generated.evolution_job import SourceOperation as _SourceOperation
from .generated.research_product_view import (
    EvolutionCandidateSummary as _EvolutionCandidateSummary,
)
from .generated.research_product_view import (
    HorizonOutcomeView as _HorizonOutcomeView,
)
from .generated.research_product_view import (
    RequirementReadinessItem as _RequirementReadinessItem,
)
from .generated.research_product_view import (
    ResearchCostBreakdown as _ResearchCostBreakdown,
)
from .generated.research_product_view import (
    ResearchCostComponent as _ResearchCostComponent,
)
from .generated.research_product_view import (
    ResearchInboxItem as _ResearchInboxItem,
)
from .generated.research_product_view import (
    ResearchInboxView as _ResearchInboxView,
)
from .generated.research_product_view import (
    ResearchObservabilityView as _ResearchObservabilityView,
)
from .generated.research_product_view import (
    ResearchValueEvaluation as _ResearchValueEvaluation,
)
from .generated.research_product_view import (
    ResearchVersionLineage as _ResearchVersionLineage,
)
from .generated.research_product_view import (
    SourceAttemptView as _SourceAttemptView,
)
from .generated.run_inspector import (
    EvidenceLineage as _EvidenceLineage,
)
from .generated.run_inspector import (
    OrchestrationLineage as _OrchestrationLineage,
)
from .generated.run_inspector import (
    TimelineItem as _TimelineItem,
)
from .generated.run_inspector import (
    VersionLineage as _VersionLineage,
)
from .generated.workbench_assets import (
    ActivePointer as _ActivePointer,
)
from .generated.workbench_assets import (
    CandidateVersion as _CandidateVersion,
)
from .generated.workbench_assets import (
    CapabilityManifest as _CapabilityManifest,
)
from .generated.workbench_assets import (
    EvaluationDatasetManifest as _EvaluationDatasetManifest,
)
from .generated.workbench_assets import (
    EvolutionOverview as _EvolutionOverview,
)
from .generated.workbench_assets import (
    Experience as _Experience,
)
from .generated.workbench_assets import (
    ExperienceCreate as _ExperienceCreate,
)
from .generated.workbench_assets import (
    ExperimentManifest as _ExperimentManifest,
)
from .generated.workbench_assets import (
    ExperimentResult as _ExperimentResult,
)
from .generated.workbench_assets import (
    FailurePattern as _FailurePattern,
)
from .generated.workbench_assets import (
    Feedback as _Feedback,
)
from .generated.workbench_assets import (
    FeedbackCreate as _FeedbackCreate,
)
from .generated.workbench_assets import (
    PromotionDecision as _PromotionDecision,
)
from .generated.workbench_assets import (
    PromotionDecisionCreate as _PromotionDecisionCreate,
)
from .generated.workbench_assets import (
    PromotionDecisionResult as _PromotionDecisionResult,
)
from .generated.workbench_assets import (
    PromotionGateCheck as _PromotionGateCheck,
)
from .generated.workbench_assets import (
    PromotionMetricDelta as _PromotionMetricDelta,
)
from .generated.workbench_assets import (
    PromotionReview as _PromotionReview,
)
from .generated.workbench_assets import (
    PromotionReviewRequest as _PromotionReviewRequest,
)
from .generated.workbench_assets import (
    ResearchMemo as _ResearchMemo,
)
from .generated.workbench_assets import (
    ResearchMemoCreate as _ResearchMemoCreate,
)
from .generated.workbench_assets import (
    WorkbenchOverview as _WorkbenchOverview,
)

# Stable public names preserve the R0/R1 import surface while the field
# definitions remain generated exclusively from the canonical schemas.
ActivePointerView = _ActivePointer
CandidateVersion = _CandidateVersion
CapabilityManifest = _CapabilityManifest
EvaluationDatasetManifest = _EvaluationDatasetManifest
EvolutionOverviewView = _EvolutionOverview
ExperienceView = _Experience
ExperienceCreate = _ExperienceCreate
ExperimentManifest = _ExperimentManifest
ExperimentResultView = _ExperimentResult
FailurePatternView = _FailurePattern
FeedbackView = _Feedback
FeedbackCreate = _FeedbackCreate
PromotionDecisionView = _PromotionDecision
PromotionDecisionCreate = _PromotionDecisionCreate
PromotionDecisionResult = _PromotionDecisionResult
PromotionGateCheckView = _PromotionGateCheck
PromotionMetricDeltaView = _PromotionMetricDelta
PromotionReviewView = _PromotionReview
PromotionReviewRequest = _PromotionReviewRequest
ResearchMemoView = _ResearchMemo
ResearchMemoCreate = _ResearchMemoCreate
WorkbenchOverviewView = _WorkbenchOverview
EvolutionJobCreate = _EvolutionJobCreate
EvolutionJobView = _EvolutionJob
EvolutionJobCountsView = _EvolutionJobCounts
EvolutionPlanningContext = _EvolutionPlanningContext
CandidateProposal = _CandidateProposal
SearchQuery = _SearchQuery
SearchEvidence = _SearchEvidence
SearchResult = _SearchResult
SourceOperationView = _SourceOperation
CapabilityOperationView = _CapabilityOperation
OperationsOverviewView = _OperationsOverview
RuntimeModeView = _RuntimeMode
ServiceHeartbeatView = _ServiceHeartbeat
HistoricalDataContracts = _historical_data.HistoricalDataContracts
DataJobCreate = _historical_data.DataJobCreate
DataJobView = _historical_data.DataJobView
DatasetCoverage = _historical_data.DatasetCoverage
CoverageManifest = _historical_data.CoverageManifest
PartitionManifest = _historical_data.PartitionManifest
EvidenceLineageView = _EvidenceLineage
OrchestrationLineageView = _OrchestrationLineage
RunTimelineItemView = _TimelineItem
VersionLineageView = _VersionLineage
EvolutionCandidateSummary = _EvolutionCandidateSummary
HorizonOutcomeView = _HorizonOutcomeView
RequirementReadinessItem = _RequirementReadinessItem
ResearchCostBreakdown = _ResearchCostBreakdown
ResearchCostComponent = _ResearchCostComponent
ResearchInboxItem = _ResearchInboxItem
ResearchInboxView = _ResearchInboxView
ResearchObservabilityView = _ResearchObservabilityView
ResearchValueEvaluation = _ResearchValueEvaluation
ResearchVersionLineage = _ResearchVersionLineage
SourceAttemptView = _SourceAttemptView
DshBridgeError = _dsh_host_bridge.DshBridgeError
DshBusinessFailure = _dsh_host_bridge.DshBusinessFailure
DshBusinessStatus = _dsh_host_bridge.DshBusinessStatus
DshHostBridgeContracts = _dsh_host_bridge.DshHostBridgeContracts
DshHostReadiness = _dsh_host_bridge.DshHostReadiness
DshResearchIntake = _dsh_host_bridge.DshResearchIntake
DshResearchIntakeAccepted = _dsh_host_bridge.DshResearchIntakeAccepted
DshRunSessionLinkView = _dsh_host_bridge.DshRunSessionLinkView
DshSessionAccepted = _dsh_host_bridge.DshSessionAccepted
DshSessionCompletion = _dsh_host_bridge.DshSessionCompletion
DshSessionPrompt = _dsh_host_bridge.DshSessionPrompt
DshSessionResult = _dsh_host_bridge.DshSessionResult
DshSessionStatus = _dsh_host_bridge.DshSessionStatus
DshSessionSubmit = _dsh_host_bridge.DshSessionSubmit
DshUpstreamIdentity = _dsh_host_bridge.DshUpstreamIdentity
AgenticResearchContracts = _agentic_research.AgenticResearchContracts
AcquisitionArtifact = _source_acquisition.Artifact
SourceAcquisition = _source_acquisition.SourceAcquisition
DocumentUpload = _research_library.DocumentUpload
DocumentUploadIntent = _research_library.DocumentUploadIntent
DocumentUploadReceipt = _research_library.DocumentUploadReceipt
DocumentUploadPolicy = _research_library.DocumentUploadPolicy
DocumentQuality = _research_library.DocumentQuality
DocumentBlock = _research_library.DocumentBlock
DocumentParseReceipt = _research_library.DocumentParseReceipt
ResearchDocument = _research_library.ResearchDocument
ResearchDocumentVersion = _research_library.ResearchDocumentVersion
CorrectionChange = _research_library.CorrectionChange
DocumentCorrection = _research_library.DocumentCorrection
AnalystClaim = _research_library.AnalystClaim
ClaimReviewCommand = _research_library.ClaimReviewCommand
ResearchRequest = _research_library.ResearchRequest
ResearchRunScope = _research_library.ResearchRunScope
ResearchRunComparisonView = _agentic_research.ResearchRunComparisonView
EventWatch = _agentic_research.EventWatch
EventWindowSample = _agentic_research.EventWindowSample
EventWindowCapture = _agentic_research.EventWindowCapture
CryptoEventWindowObservation = _agentic_research.CryptoEventWindowObservation
CryptoEventWindowFailure = _agentic_research.CryptoEventWindowFailure
CryptoEventWindowPayload = _agentic_research.CryptoEventWindowPayload
CausalCase = _agentic_research.CausalCase
CausalLink = _agentic_research.CausalLink
DecisionGraph = _agentic_research.DecisionGraph
DecisionGraphEdge = _agentic_research.DecisionGraphEdge
DecisionGraphNode = _agentic_research.DecisionGraphNode
ResearchClaimTransition = _agentic_research.ResearchClaimTransition
ResearchRolePacket = _agentic_research.ResearchRolePacket
ConflictItem = _agentic_research.ConflictItem
ErrorProvenance = _agentic_research.ErrorProvenance
CoverageAssessment = _agentic_research.CoverageAssessment
DomainPackManifest = _agentic_research.DomainPackManifest
EvidenceCandidate = _agentic_research.EvidenceCandidate
ResearchInputEvidence = _agentic_research.ResearchInputEvidence
EvidenceGap = _agentic_research.EvidenceGap
EvidenceRequirement = _agentic_research.EvidenceRequirement
ExecutionBudget = _agentic_research.ExecutionBudget
ExecutionBudgetV2 = _agentic_research.ExecutionBudgetV2
HorizonDecision = _agentic_research.HorizonDecision
ProductExtensionManifest = _agentic_research.ProductExtensionManifest
ProviderAttempt = _agentic_research.ProviderAttempt
ProviderRoute = _agentic_research.ProviderRoute
ResearchCapabilityManifest = _agentic_research.ResearchCapabilityManifest
ResearchCapabilityInvocation = _agentic_research.ResearchCapabilityInvocation
ResearchCapabilityQuery = _agentic_research.ResearchCapabilityQuery
ResearchCapabilityResult = _agentic_research.ResearchCapabilityResult
ResearchSourcePolicy = _agentic_research.ResearchSourcePolicy
ResearchSourceRegistry = _agentic_research.ResearchSourceRegistry
ResearchRunQueued = _agentic_research.ResearchRunQueued
ResearchEvaluationCapabilityFixture = _agentic_research.ResearchEvaluationCapabilityFixture
ResearchEvaluationCase = _agentic_research.ResearchEvaluationCase
ResearchEvaluationOutcomeLabel = _agentic_research.ResearchEvaluationOutcomeLabel
ResearchRuntimeCaseReport = _agentic_research.ResearchRuntimeCaseReport
ResearchRuntimeComparison = _agentic_research.ResearchRuntimeComparison
ResearchRuntimeSummary = _agentic_research.ResearchRuntimeSummary
ResearchPlan = _agentic_research.ResearchPlan
ResearchRound = _agentic_research.ResearchRound
ResearchRoundView = _agentic_research.ResearchRoundView
ResearchRunCommand = _agentic_research.ResearchRunCommand
ResearchRunCommandResult = _agentic_research.ResearchRunCommandResult
ResearchRunCommandReceipt = _agentic_research.ResearchRunCommandReceipt
ResearchGatePolicy = _research_policy.ResearchGatePolicy
ResearchRubricPolicy = _research_policy.ResearchRubricPolicy
ResearchKernelSafeguards = _research_policy.ResearchKernelSafeguards
ResearchTaskScope = _research_policy.ResearchTaskScope
ResearchTaskScopeV2 = _research_policy.ResearchTaskScopeV2
ResearchFactoryEventSource = _research_binding.ResearchFactoryEventSource
ResearchFactoryStudySource = _research_binding.ResearchFactoryStudySource
ResearchFactoryStudySourceV2 = _research_binding.ResearchFactoryStudySourceV2
ResearchFactoryTriggerSource = _research_binding.ResearchFactoryTriggerSource
ResearchFactoryLibraryScopeSource = _research_binding.ResearchFactoryLibraryScopeSource
ResearchFactoryScopeSource = _research_binding.ResearchFactoryScopeSource
ResearchFactoryScopeSourceV2 = _research_binding.ResearchFactoryScopeSourceV2
ResearchPackEvidenceRequirement = _research_binding.ResearchPackEvidenceRequirement
ResearchPackEvidencePolicyV3 = _research_binding.ResearchPackEvidencePolicyV3
ResearchPackEvidenceResource = _research_binding.ResearchPackEvidenceResource
ResearchInstructionFile = _research_binding.ResearchInstructionFile
ResearchFrozenRoleInstructions = _research_binding.ResearchFrozenRoleInstructions
ResearchInstructionsSnapshotV2 = _research_binding.ResearchInstructionsSnapshotV2
ResearchPackDefinitionV2 = _research_binding.ResearchPackDefinitionV2
ResearchRequestPackReceiptV2 = _research_binding.ResearchRequestPackReceiptV2
ResearchTaskReceiptV2 = _research_binding.ResearchTaskReceiptV2
ResearchCompiledPolicyReceiptV1 = _research_binding.ResearchCompiledPolicyReceiptV1
ResearchLibraryContextInputReceipt = _research_binding.ResearchLibraryContextInputReceipt
ResearchPolicyResource = _research_policy.ResearchPolicyResource
ResearchRoleRubricBinding = _research_policy.ResearchRoleRubricBinding
ResearchCompiledPolicy = _research_policy.ResearchCompiledPolicy
ResearchCompiledRule = _research_policy.ResearchCompiledRule
ResearchPolicyCodeIdentity = _research_policy.ResearchPolicyCodeIdentity
ResearchPolicyMethodBinding = _research_policy.ResearchPolicyMethodBinding
ResearchPolicyCheckReceipt = _research_policy.ResearchPolicyCheckReceipt
ResearchPolicyCheckItem = _research_policy.ResearchPolicyCheckItem
ResearchPolicyEvidenceRef = _research_policy.ResearchPolicyEvidenceRef
ResearchActionSummary = _agentic_research.ResearchActionSummary
ResearchOutlook = _agentic_research.ResearchOutlook
ResearchStrategyReceipt = _agentic_research.ResearchStrategyReceipt
ResearchMarketView = _agentic_research.ResearchMarketView
ResearchExecutionView = _agentic_research.ResearchExecutionView
ResearchProcessStep = _agentic_research.ResearchProcessStep
ResearchRunDetailView = _agentic_research.ResearchRunDetailView
ResearchRunView = _agentic_research.ResearchRunView
ResearchSessionRequest = _agentic_research.ResearchSessionRequest
ResearchSessionResult = _agentic_research.ResearchSessionResult
ResearchSynthesisCandidate = _agentic_research.ResearchSynthesisCandidate
ResearchSnapshotManifest = _agentic_research.ResearchSnapshotManifest
ResearchStopReason = _agentic_research.ResearchStopReason
ResearchTask = _agentic_research.ResearchTask
ResearchTaskPolicy = _agentic_research.ResearchTaskPolicy
ContextExclusion = _agentic_research.ContextExclusion
ResearchContextManifest = _agentic_research.ResearchContextManifest
ResearchReview = _agentic_research.ResearchReview
ResearchTraceEvent = _agentic_research.ResearchTraceEvent
RoleProfile = _agentic_research.RoleProfile
# Cross-file JSON Schema references are inlined by datamodel-codegen. Export the
# exact class used by ResearchCapabilityResult so callers never see two
# structurally-equal but Pydantic-incompatible FactEnvelope identities.
FactEnvelope = _agentic_research.FactEnvelope
ToolInvocation = _agentic_research.ToolInvocation
ToolResultSummary = _agentic_research.ToolResultSummary


class SourceType(StrEnum):
    manual = "manual"
    transcript = "transcript"
    official_feed = "official_feed"
    web = "web"
    document = "document"


class RunStatus(StrEnum):
    admitted = "admitted"
    running = "running"
    completed = "completed"
    degraded = "degraded"
    failed = "failed"
    cancelled = "cancelled"


class GateStatus(StrEnum):
    publish = "publish"
    degraded = "degraded"
    research_only = "research_only"
    reject = "reject"


class Direction(StrEnum):
    long = "long"
    short = "short"
    neutral = "neutral"
    no_trade = "no_trade"


class MacroEventPayload(BaseModel):
    """Point-in-time macro event fields carried by calendar/release sources."""

    model_config = ConfigDict(extra="forbid")

    external_event_id: str = Field(min_length=1, max_length=256)
    family: str = Field(min_length=1, max_length=128)
    scheduled_at: datetime
    # Legacy transport time remains for storage compatibility. Only exact
    # timestamps authorize minute-level scheduling; dates have no such instant.
    scheduled_date: date | None = None
    time_precision: Literal["exact", "date", "unknown"] = "unknown"
    source_timezone: str | None = Field(default=None, max_length=64)
    source_authority: str = Field(default="official", min_length=1, max_length=32)
    unit: str | None = Field(default=None, max_length=32)
    prior_value: float | None = None
    consensus_value: float | None = None
    actual_value: float | None = None
    vintage_at: datetime | None = None
    revision_flag: bool = False
    status: str = Field(default="scheduled", pattern=r"^(scheduled|released|revised|cancelled)$")

    @model_validator(mode="after")
    def require_schedule_precision(self) -> MacroEventPayload:
        if self.time_precision == "exact" and self.scheduled_at.utcoffset() is None:
            raise ValueError("exact schedule requires an aware timestamp")
        if self.time_precision == "date" and self.scheduled_date is None:
            raise ValueError("date precision requires scheduled_date")
        return self


class MacroEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["macro-event-view.v1"] = "macro-event-view.v1"
    macro_event_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    scheduled_at: datetime | None
    scheduled_date: date | None = None
    time_precision: Literal["exact", "date", "unknown"] = "unknown"
    source_timezone: str | None = None
    invalidated_at: datetime | None = None
    correction_ref: str | None = None
    source_id: str = Field(min_length=1)
    source_url: str | None = None
    source_authority: str = Field(min_length=1)
    unit: str | None = None
    prior_value: float | None = None
    consensus_value: float | None = None
    actual_value: float | None = None
    surprise: float | None = None
    surprise_z: float | None = None
    revision_flag: bool = False
    status: Literal["scheduled", "released", "revised", "cancelled"]
    updated_at: datetime


class MacroEventVintageView(BaseModel):
    """Immutable point-in-time release used by calendar history and review."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["macro-event-vintage-view.v1"] = "macro-event-vintage-view.v1"
    vintage_id: str = Field(min_length=1)
    macro_event_id: str = Field(min_length=1)
    observed_at: datetime
    scheduled_at: datetime | None = None
    scheduled_date: date | None = None
    time_precision: Literal["exact", "date", "unknown"] = "unknown"
    source_timezone: str | None = None
    prior_value: float | None = None
    consensus_value: float | None = None
    actual_value: float | None = None
    surprise: float | None = None
    surprise_z: float | None = None
    revision_flag: bool = False
    status: Literal["scheduled", "released", "revised", "cancelled"]
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class TextEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "text-envelope.v1"
    source_id: str = Field(min_length=1)
    source_type: SourceType
    observed_at: datetime
    published_at: datetime | None = None
    received_at: datetime
    raw_text: str = Field(min_length=1, max_length=200_000)
    language: str = Field(min_length=2, max_length=16)
    event_hint: str | None = Field(default=None, max_length=128)
    event_family: str | None = Field(default=None, max_length=128)
    scheduled_at: datetime | None = None
    source_url: HttpUrl | None = None
    revision_of: str | None = None
    macro_event: MacroEventPayload | None = None
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200_000)
    source_id: str = "manual-text"
    source_type: SourceType = SourceType.manual
    observed_at: datetime | None = None
    published_at: datetime | None = None
    language: str = "zh"
    event_hint: str | None = Field(default=None, max_length=128)
    source_url: HttpUrl | None = None


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    version: str = Field(min_length=1)
    capabilities: tuple[str, ...] = ()
    authority_level: str = "unverified"
    poll_interval_seconds: float = Field(default=60, gt=0)
    max_batch: int = Field(default=50, gt=0, le=500)
    allowed_domains: tuple[str, ...] = ()
    enabled: bool = True


class SourceHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    status: str = "unknown"
    cursor: str | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    next_poll_at: datetime | None = None


class ProductHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    running_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    sources: list[SourceHealth] = Field(default_factory=list)


class PilotReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(pass|fail|warning)$")
    detail: str = Field(min_length=1)
    error_code: str | None = None


class PilotReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "pilot-readiness.v1"
    status: str = Field(pattern=r"^(ready|not_ready)$")
    checked_at: datetime
    pilot_mode: bool
    notification_channel: str = Field(pattern=r"^(local|email)$")
    source_ids: tuple[str, ...] = ()
    checks: tuple[PilotReadinessCheck, ...]
    live_canaries_required: tuple[str, ...] = ()


class MarketQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str = Field(min_length=1)
    observed_at: datetime
    received_at: datetime
    bid: float | None = Field(default=None, ge=0)
    ask: float | None = Field(default=None, ge=0)
    last: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    source_id: str = Field(min_length=1)
    quality_status: str = "observed"
    benchmark: str = "first_executable"


class NotificationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    dedupe_key: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    created_at: datetime


class Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forecast_id: str
    artifact_id: str
    instrument: str
    horizon: str
    direction: Direction
    probability: float = Field(ge=0, le=1)
    trigger: str
    invalidation: str
    expires_at: datetime


class ForecastAuditView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["forecast-audit-view.v1"] = "forecast-audit-view.v1"
    forecast_id: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    forecast_direction: Direction
    strategy_version: str | None = None
    forecasted_at: datetime | None = None
    as_of: str | None = None
    input_snapshot_id: str | None = None
    input_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_cutoff_at: datetime | None = None
    feature_snapshot: dict[str, float] = Field(default_factory=dict)
    trailing_direction: Literal["bullish", "bearish", "flat", "unavailable"]
    same_as_trailing_trend: bool | None = None
    forecasted_before_outcome: bool | None = None
    immutable_snapshot: bool


class StrategyCalibrationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["strategy-calibration-view.v1"] = (
        "strategy-calibration-view.v1"
    )
    receipt_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sample_count: int = Field(ge=0)
    evaluated_sample_count: int = Field(ge=0)
    fold_count: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    accuracy_lower_bound: float = Field(ge=0, le=1)
    brier_score: float = Field(ge=0, le=1)
    baseline_brier_score: float = Field(ge=0, le=1)
    log_loss: float = Field(ge=0)
    net_return_pct: float
    max_drawdown_pct: float = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    max_regime_share: float = Field(ge=0, le=1)
    passed: bool
    failure_reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime


class ReviewScheduleView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["review-schedule-view.v1"] = "review-schedule-view.v1"
    review_plan_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    horizon: Literal["30m", "24h", "72h"]
    trigger_type: Literal[
        "next_review", "macro_event", "invalidation_evidence", "outcome_due"
    ]
    trigger_ref: str = Field(min_length=1)
    trigger_at: datetime
    status: Literal["planned", "admitted", "completed", "skipped", "failed"]
    child_run_id: str | None = None
    failure_code: str | None = None
    replacement_run_id: str | None = None
    replacement_review_plan_id: str | None = None
    retry_of_plan_id: str | None = None
    retry_attempt: int = Field(default=0, ge=0, le=2)
    updated_at: datetime


class GateDecision(BaseModel):
    rule_id: str
    status: str
    reason_code: str
    input_hash: str


class ArtifactView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    event_id: str
    gate_status: GateStatus
    headline: str
    summary: str
    facts: list[str]
    inferences: list[str]
    counter_thesis: str
    uncertainty: list[str]
    transmission_chain: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    forecasts: list[Forecast] = Field(default_factory=list)
    gate_decisions: list[GateDecision] = Field(default_factory=list)
    created_at: datetime


class RunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    event_id: str
    status: RunStatus
    strategy_version: str
    runtime_version: str = "unknown"
    snapshot_id: str | None = None
    artifact_id: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    error_code: str | None = None
    headline: str | None = None
    gate_status: GateStatus | None = None


class CallView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    run_id: str
    role: str
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = None
    runtime_id: str | None = None
    runtime_version: str | None = None
    provider_id: str | None = None
    model: str | None = None
    api_mode: str | None = None
    schema_version: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_status: str = "unknown"
    pricing_version: str | None = None
    error_code: str | None = None
    retryable: bool = False


class StepView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    run_id: str
    step_name: str
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = None
    error_code: str | None = None


class RunTimelineView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    items: list[RunTimelineItemView] = Field(default_factory=list)


class RunInspectorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunView
    timeline: list[RunTimelineItemView]
    steps: list[StepView]
    calls: list[CallView]
    artifact: ArtifactView | None = None
    evaluation_count: int = 0
    evaluations: list[EvaluationView] = Field(default_factory=list)
    snapshot_cutoff_at: datetime | None = None
    snapshot_hash: str | None = None
    evidence_lineage: list[EvidenceLineageView] = Field(default_factory=list)
    versions: VersionLineageView
    orchestration: OrchestrationLineageView


class InboxView(BaseModel):
    pending_count: int
    running_count: int
    latest: list[RunView]


class DecisionDeskSummary(BaseModel):
    inbox: InboxView
    published_count_30d: int
    forecast_count: int
    evaluated_count: int
    health_status: str
    active_strategy: str
    active_pack: str


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool = False
    request_id: str


class OutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forecast_id: str
    return_pct: float
    direction_correct: bool
    fees: float = 0
    slippage: float = 0
    quality_status: str = "estimated"


class EvaluationView(BaseModel):
    evaluation_id: str
    forecast_id: str
    brier_score: float
    net_return_pct: float
    direction_correct: bool
    label_status: str
    evaluated_at: datetime


# New execution declarations retain their own strict canonical base.
ResearchBundlePackRowIdentity = _research_execution_binding.ResearchBundlePackRowIdentity
ResearchBundleTaskRowIdentity = _research_execution_binding.ResearchBundleTaskRowIdentity
ResearchBundleCompiledRowIdentity = _research_execution_binding.ResearchBundleCompiledRowIdentity
ResearchPreparedBundleIdentity = _research_execution_binding.ResearchPreparedBundleIdentity
ResearchExecutionMethodDefinitionV2 = _research_execution_binding.ResearchExecutionMethodDefinitionV2
ResearchExecutionMethodReceiptV2 = _research_execution_binding.ResearchExecutionMethodReceiptV2
ResearchExecutionRequestReceiptV2 = _research_execution_binding.ResearchExecutionRequestReceiptV2
ResearchExecutionPromptV2 = _research_execution_binding.ResearchExecutionPromptV2
ResearchExecutionPromptIntegrityV2 = _research_execution_binding.ResearchExecutionPromptIntegrityV2


# New time declarations do not upgrade historical source or execution authority.
from .generated import research_context_selection as _context_selection  # noqa: E402

ResearchContextTaskAnchorV1 = _context_selection.ResearchContextTaskAnchorV1
ResearchContextSelectorIdentityV1 = _context_selection.ResearchContextSelectorIdentityV1
ResearchContextDeclaredPriorRefV1 = _context_selection.ResearchContextDeclaredPriorRefV1
ResearchContextEvidenceRowDeclarationV1 = _context_selection.ResearchContextEvidenceRowDeclarationV1
ResearchContextInputWitnessV1 = _context_selection.ResearchContextInputWitnessV1
ResearchContextSelectionRowIdentityV1 = _context_selection.ResearchContextSelectionRowIdentityV1
ResearchContextExecutionRowIdentityV1 = _context_selection.ResearchContextExecutionRowIdentityV1
ResearchContextIntegrityRowIdentityV1 = _context_selection.ResearchContextIntegrityRowIdentityV1
ResearchContextSelectionReceiptV1 = _context_selection.ResearchContextSelectionReceiptV1
ResearchContextSelectionExecutionLinkV1 = _context_selection.ResearchContextSelectionExecutionLinkV1


# V2 ranking declarations are separate from V1 source/execution authority.
from .generated import research_context_selection_v2 as _context_selection_v2  # noqa: E402

ResearchContextSelectorIdentityV2 = _context_selection_v2.ResearchContextSelectorIdentityV2
ResearchContextSelectionReceiptV2 = _context_selection_v2.ResearchContextSelectionReceiptV2


# V3 binds a declared structured Graph prior; it does not authenticate SQL.
from .generated import research_context_selection_v3 as _context_selection_v3  # noqa: E402

ResearchContextGraphRowRefV3 = _context_selection_v3.ResearchContextGraphRowRefV3
ResearchContextGraphPriorRefV3 = _context_selection_v3.ResearchContextGraphPriorRefV3
ResearchContextSelectionReceiptV3 = _context_selection_v3.ResearchContextSelectionReceiptV3


from .generated import research_context_execution as _context_execution  # noqa: E402

ResearchContextSelectionRowIdentityV3 = _context_execution.ResearchContextSelectionRowIdentityV3
ResearchRequiredContextSelectionBindingV1 = _context_execution.ResearchRequiredContextSelectionBindingV1
ResearchExecutionMethodDefinitionV3 = _context_execution.ResearchExecutionMethodDefinitionV3
ResearchExecutionMethodReceiptV3 = _context_execution.ResearchExecutionMethodReceiptV3
ResearchExecutionRequestReceiptV3 = _context_execution.ResearchExecutionRequestReceiptV3
ResearchContextExecutionRowIdentityV3 = _context_execution.ResearchContextExecutionRowIdentityV3
ResearchContextSelectionExecutionLinkV2 = _context_execution.ResearchContextSelectionExecutionLinkV2


from .generated import read_command_receipt as _read_command_receipt  # noqa: E402

ReadCommandReceipt = _read_command_receipt.ReadCommandReceipt


from .generated import revision_feedback_evaluation as _revision_feedback  # noqa: E402

RevisionFeedbackSourceWitness = _revision_feedback.RevisionFeedbackSourceWitness
RevisionFeedbackEvaluationBinding = _revision_feedback.RevisionFeedbackEvaluationBinding
RevisionFeedbackEvaluation = _revision_feedback.RevisionFeedbackEvaluation
RevisionFeedbackEvaluationBasis = _revision_feedback.RevisionFeedbackEvaluationBasis


# Provider callback shapes are distinct from server source authentication.
from .generated import provider_input as _provider_input  # noqa: E402

ResearchProviderInputRef = _provider_input.ResearchProviderInputRef
ResearchProviderCallbackReference = _provider_input.ResearchProviderCallbackReference
ResearchProviderHostScope = _provider_input.ResearchProviderHostScope
ResearchProviderInputSubmit = _provider_input.ResearchProviderInputSubmit
ResearchProviderInputRecorded = _provider_input.ResearchProviderInputRecorded
ResearchProviderToolEmissionSubmit = _provider_input.ResearchProviderToolEmissionSubmit
ResearchProviderToolEmissionRecorded = _provider_input.ResearchProviderToolEmissionRecorded
ResearchProviderFinishSubmit = _provider_input.ResearchProviderFinishSubmit
ResearchProviderFinishRecorded = _provider_input.ResearchProviderFinishRecorded
ResearchProviderCallbackAck = _provider_input.ResearchProviderCallbackAck


from .generated import independent_review as _independent_review  # noqa: E402

IndependentReviewIssueV2 = _independent_review.IndependentReviewIssueV2
IndependentReviewReceiptV2 = _independent_review.IndependentReviewReceiptV2
