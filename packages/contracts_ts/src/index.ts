import { z } from 'zod'
export {
  researchFactoryEventSourceSchema, researchFactoryStudySourceSchema, researchFactoryStudySourceV2Schema, researchFactoryScopeSourceV2Schema,
  researchFactoryTriggerSourceSchema, researchFactoryLibraryScopeSourceSchema,
  researchFactoryScopeSourceSchema, researchPackEvidenceRequirementSchema,
  researchPackEvidencePolicyV3Schema, researchPackEvidenceResourceSchema,
  researchInstructionFileSchema, researchFrozenRoleInstructionsSchema,
  researchInstructionsSnapshotV2Schema, researchPackDefinitionV2Schema,
  researchRequestPackReceiptV2Schema, researchTaskReceiptV2Schema,
  researchCompiledPolicyReceiptV1Schema,
  researchLibraryContextInputReceiptSchema,
} from './generated/r2'
export type {
  ResearchFactoryEventSource, ResearchFactoryStudySource, ResearchFactoryStudySourceV2, ResearchFactoryScopeSourceV2,
  ResearchFactoryTriggerSource, ResearchFactoryLibraryScopeSource,
  ResearchFactoryScopeSource, ResearchPackEvidenceRequirement,
  ResearchPackEvidencePolicyV3, ResearchPackEvidenceResource,
  ResearchInstructionFile, ResearchFrozenRoleInstructions,
  ResearchInstructionsSnapshotV2, ResearchPackDefinitionV2,
  ResearchRequestPackReceiptV2, ResearchTaskReceiptV2,
  ResearchCompiledPolicyReceiptV1,
  ResearchLibraryContextInputReceipt,
} from './generated/r2'
export {
  researchGatePolicySchema, researchRubricPolicySchema, researchKernelSafeguardsSchema,
  researchTaskScopeSchema, researchTaskScopeV2Schema, researchPolicyResourceSchema, researchRoleRubricBindingSchema,
  researchCompiledPolicySchema, researchCompiledRuleSchema, researchPolicyCodeIdentitySchema,
  researchPolicyMethodBindingSchema, researchPolicyCheckReceiptSchema, researchPolicyCheckItemSchema,
  researchPolicyEvidenceRefSchema,
} from './generated/r2'
export type {
  ResearchGatePolicy, ResearchRubricPolicy, ResearchKernelSafeguards,
  ResearchTaskScope, ResearchTaskScopeV2, ResearchPolicyResource, ResearchRoleRubricBinding,
  ResearchCompiledPolicy, ResearchCompiledRule, ResearchPolicyCodeIdentity,
  ResearchPolicyMethodBinding, ResearchPolicyCheckReceipt, ResearchPolicyCheckItem,
  ResearchPolicyEvidenceRef,
} from './generated/r2'
export {
  usageCollectionViewSchema,
  researchUsageSnapshotSchema, researchUsageSnapshotPageSchema,
  researchUsageViewSchema,
  researchMarketCatalogSchema,
  eventAssetViewSchema,
  reportDeliveryPageSchema, readReceiptViewSchema, studyTrackingReceiptSchema,
  analyticalOutlookListViewSchema, analyticalOutlookDetailViewSchema,
  analyticalCohortListViewSchema, publicationSeparationViewSchema,
  factVersionListViewSchema, factVersionViewSchema, archivedInputViewSchema,
  studyListViewSchema, studyTimelineViewSchema,
  sourceHistoryViewSchema, rawCaptureViewSchema, coverageManifestSchema,
} from './generated/r2'
export type {
  UsageCollectionView,
  ResearchUsageSnapshot, ResearchUsageSnapshotPage,
  ResearchUsageView,
  ResearchMarketScope, ResearchMarketCatalog,
  AnalyticalOutlookView, AnalyticalOutlookDetailView, AnalyticalCohortView,
  FactVersionView, StudyView, StudyTimelineView,
} from './generated/r2'
import {
  evidenceLineageSchema,
  orchestrationLineageSchema,
  runTimelineItemSchema,
  versionLineageSchema,
  evolutionJobSchema,
  evolutionJobCountsSchema,
  evolutionJobCreateSchema,
  operationsOverviewSchema,
  runtimeModeSchema,
  serviceHeartbeatSchema,
  candidateProposalSchema,
  capabilityOperationSchema,
  evolutionPlanningContextSchema,
  searchEvidenceSchema,
  searchQuerySchema,
  searchResultSchema,
  sourceOperationSchema,
  evolutionCandidateSummarySchema,
  horizonOutcomeViewSchema,
  requirementReadinessItemSchema,
  researchCostBreakdownSchema,
  researchCostComponentSchema,
  researchInboxItemSchema,
  researchInboxViewSchema,
  researchObservabilityViewSchema,
  researchValueEvaluationSchema,
  researchVersionLineageSchema,
  sourceAttemptViewSchema,
} from './generated/r2'

export {
  dshBridgeErrorSchema,
  dshBusinessFailureSchema,
  dshBusinessStatusSchema,
  dshBrowserStatusSchema,
  dshHostReadinessSchema,
  dshResearchIntakeAcceptedSchema,
  dshResearchIntakeSchema,
  dshRunSessionLinkViewSchema,
  dshSessionAcceptedSchema,
  dshSessionCompletionSchema,
  dshSessionPromptSchema,
  dshSessionResultSchema,
  dshSessionStatusSchema,
  dshModelDispatchBindingSchema,
  dshSessionSubmitSchema,
  dshUpstreamIdentitySchema,
  causalCaseSchema,
  causalLinkSchema,
  decisionGraphSchema,
  decisionGraphEdgeSchema,
  decisionGraphNodeSchema,
  conflictItemSchema,
  coverageAssessmentSchema,
  domainPackManifestSchema,
  evidenceCandidateSchema,
  evidenceGapSchema,
  evidenceRequirementSchema,
  executionBudgetSchema,
  executionBudgetV2Schema,
  horizonDecisionSchema,
  productExtensionManifestSchema,
  errorProvenanceSchema,
  researchCapabilityInvocationSchema,
  researchCapabilityQuerySchema,
  researchCapabilityManifestSchema,
  researchCapabilityResultSchema,
  researchInputEvidenceSchema,
  researchPlanSchema,
  researchActionSummarySchema,
  researchProcessStepSchema,
  researchRoundSchema,
  researchRunCommandSchema,
  researchRunCommandResultSchema,
  researchRunCommandReceiptSchema,
  researchRunComparisonViewSchema,
  researchRunDetailViewSchema,
  researchRunQueuedSchema,
  researchRunViewSchema,
  researchSessionRequestSchema,
  researchSessionResultSchema,
  researchSynthesisCandidateSchema,
  researchStopReasonSchema,
  researchTaskSchema,
  researchTraceEventSchema,
  roleProfileSchema,
  toolInvocationSchema,
  toolResultSummarySchema,
  analystClaimSchema,
  claimReviewCommandSchema,
  documentBlockSchema,
  documentCorrectionSchema,
  documentParseReceiptSchema,
  documentQualitySchema,
  documentUploadSchema,
  documentUploadIntentSchema,
  documentUploadReceiptSchema,
  documentUploadPolicySchema,
  researchDocumentSchema,
  researchDocumentVersionSchema,
  researchRequestSchema,
  researchRunScopeSchema,
} from './generated/r2'

export type {
  DshBridgeError,
  DshBusinessFailure,
  DshBusinessStatus,
  DshBrowserStatus,
  DshHostReadiness,
  DshResearchIntake,
  DshResearchIntakeAccepted,
  DshRunSessionLinkView,
  DshSessionAccepted,
  DshSessionCompletion,
  DshSessionPrompt,
  DshSessionResult,
  DshSessionStatus,
  DshModelDispatchBinding,
  DshSessionSubmit,
  DshUpstreamIdentity,
  CausalCase,
  CausalLink,
  DecisionGraph,
  DecisionGraphEdge,
  DecisionGraphNode,
  ConflictItem,
  CoverageAssessment,
  DomainPackManifest,
  EvidenceCandidate,
  EvidenceGap,
  EvidenceRequirement,
  ExecutionBudget,
  ExecutionBudgetV2,
  HorizonDecision,
  ProductExtensionManifest,
  ProviderAttempt,
  ProviderRoute,
  ResearchCapabilityManifest,
  ResearchInputEvidence,
  ResearchPlan,
  ResearchActionSummary,
  ResearchProcessStep,
  ResearchRound,
  ResearchRunCommand,
  ResearchRunCommandResult,
  ResearchRunCommandReceipt,
  ResearchRunComparisonView,
  ResearchRunDetailView,
  ResearchRunQueued,
  ResearchRunView,
  ResearchSessionRequest,
  ResearchSessionResult,
  ResearchSynthesisCandidate,
  ResearchStopReason,
  ResearchTask,
  ResearchTraceEvent,
  RoleProfile,
  ToolInvocation,
  ToolResultSummary,
  AnalystClaim,
  ClaimReviewCommand,
  DocumentBlock,
  DocumentCorrection,
  DocumentParseReceipt,
  DocumentQuality,
  DocumentUpload,
  DocumentUploadIntent,
  DocumentUploadReceipt,
  DocumentUploadPolicy,
  ResearchDocument,
  ResearchDocumentVersion,
  ResearchRequest,
  ResearchRunScope,
} from './generated/r2'

export {
  activePointerSchema,
  candidateVersionSchema,
  capabilityManifestSchema,
  evaluationDatasetSchema,
  evolutionOverviewSchema,
  experienceCreateSchema,
  experienceSchema,
  experimentResultSchema,
  experimentSchema,
  failurePatternSchema,
  feedbackCreateSchema,
  feedbackSchema,
  promotionDecisionCommandSchema,
  promotionDecisionResultSchema,
  promotionDecisionSchema,
  promotionGateCheckSchema,
  promotionMetricDeltaSchema,
  promotionReviewRequestSchema,
  promotionReviewSchema,
  researchMemoCreateSchema,
  researchMemoSchema,
  workbenchOverviewSchema,
  evolutionJobSchema,
  evolutionJobCountsSchema,
  evolutionJobCreateSchema,
  operationsOverviewSchema,
  runtimeModeSchema,
  serviceHeartbeatSchema,
  candidateProposalSchema,
  capabilityOperationSchema,
  evolutionPlanningContextSchema,
  searchEvidenceSchema,
  searchQuerySchema,
  searchResultSchema,
  sourceOperationSchema,
  evolutionCandidateSummarySchema,
  horizonOutcomeViewSchema,
  requirementReadinessItemSchema,
  researchCostBreakdownSchema,
  researchCostComponentSchema,
  researchInboxItemSchema,
  researchInboxViewSchema,
  researchObservabilityViewSchema,
  researchValueEvaluationSchema,
  researchVersionLineageSchema,
  sourceAttemptViewSchema,
  evidenceLineageSchema,
  orchestrationLineageSchema,
  runTimelineItemSchema,
  versionLineageSchema,
} from './generated/r2'

export const runStatusSchema = z.enum(['admitted', 'running', 'completed', 'degraded', 'failed', 'cancelled'])
export const gateStatusSchema = z.enum(['publish', 'degraded', 'research_only', 'reject'])
export const directionSchema = z.enum(['long', 'short', 'neutral', 'no_trade'])
export const sourceManifestSchema = z.object({
  source_id: z.string().min(1), source_type: z.string().min(1), version: z.string().min(1),
  capabilities: z.array(z.string()), authority_level: z.string(),
  poll_interval_seconds: z.number().positive(), max_batch: z.number().int().positive(),
  allowed_domains: z.array(z.string()).default([]), enabled: z.boolean(),
})
export const sourceHealthSchema = z.object({
  source_id: z.string(), status: z.string(), cursor: z.string().nullable(),
  last_success_at: z.string().nullable(), last_error_at: z.string().nullable(),
  consecutive_failures: z.number().int().nonnegative(), latency_ms: z.number().nullable(),
  error_code: z.string().nullable(), next_poll_at: z.string().nullable(),
})
export const marketQuoteSchema = z.object({
  instrument: z.string(), observed_at: z.string(), received_at: z.string(),
  bid: z.number().nullable(), ask: z.number().nullable(), last: z.number().nullable(),
  volume: z.number().nullable(), source_id: z.string(),
  quality_status: z.enum(['observed', 'estimated', 'unavailable']),
  benchmark: z.enum(['first_executable', 'vwap_1m', 'close_1m', 'last', 'none']),
})
export const macroEventViewSchema = z.object({
  schema_version: z.literal('macro-event-view.v1'), macro_event_id: z.string().min(1),
  family: z.string().min(1), scheduled_at: z.string().nullable(), source_id: z.string().min(1),
  scheduled_date: z.string().nullable().optional(),
  time_precision: z.enum(['exact', 'date', 'unknown']).optional(),
  source_timezone: z.string().nullable().optional(),
  invalidated_at: z.string().nullable().optional(), correction_ref: z.string().nullable().optional(),
  source_url: z.string().nullable(), source_authority: z.string().min(1), unit: z.string().nullable(),
  prior_value: z.number().nullable(), consensus_value: z.number().nullable(), actual_value: z.number().nullable(),
  surprise: z.number().nullable(), surprise_z: z.number().nullable(), revision_flag: z.boolean(),
  status: z.enum(['scheduled', 'released', 'revised', 'cancelled']), updated_at: z.string(),
})
export const notificationMessageSchema = z.object({
  artifact_id: z.string(), channel: z.string(), dedupe_key: z.string(),
  subject: z.string(), body: z.string(), created_at: z.string(),
})
export const productHealthSchema = z.object({
  status: z.enum(['ok', 'degraded']), running_runs: z.number().int().nonnegative(),
  failed_runs: z.number().int().nonnegative(), sources: z.array(sourceHealthSchema),
})
export const pilotReadinessCheckSchema = z.object({
  check_id: z.string().min(1), status: z.enum(['pass', 'fail', 'warning']),
  detail: z.string().min(1), error_code: z.string().nullable(),
})
export const pilotReadinessReportSchema = z.object({
  schema_version: z.literal('pilot-readiness.v1'), status: z.enum(['ready', 'not_ready']),
  checked_at: z.string(), pilot_mode: z.boolean(),
  notification_channel: z.enum(['local', 'email']), source_ids: z.array(z.string()),
  checks: z.array(pilotReadinessCheckSchema), live_canaries_required: z.array(z.string()),
})

export const forecastSchema = z.object({
  forecast_id: z.string(), artifact_id: z.string(), instrument: z.string(), horizon: z.string(),
  direction: directionSchema, probability: z.number().min(0).max(1), trigger: z.string(),
  invalidation: z.string(), expires_at: z.string(),
})
export const forecastAuditViewSchema = z.object({
  schema_version: z.literal('forecast-audit-view.v1'), forecast_id: z.string().min(1),
  horizon: z.string().min(1), forecast_direction: directionSchema,
  strategy_version: z.string().nullable(), forecasted_at: z.string().nullable(), as_of: z.string().nullable(),
  input_snapshot_id: z.string().nullable(), input_fingerprint: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
  evidence_cutoff_at: z.string().nullable(), feature_snapshot: z.record(z.number()),
  trailing_direction: z.enum(['bullish', 'bearish', 'flat', 'unavailable']),
  same_as_trailing_trend: z.boolean().nullable(), forecasted_before_outcome: z.boolean().nullable(),
  immutable_snapshot: z.boolean(),
})
export const strategyCalibrationViewSchema = z.object({
  schema_version: z.literal('strategy-calibration-view.v1'), receipt_id: z.string().min(1),
  strategy_version: z.string().min(1), horizon: z.string().min(1),
  dataset_hash: z.string().regex(/^[a-f0-9]{64}$/), sample_count: z.number().int().nonnegative(),
  evaluated_sample_count: z.number().int().nonnegative(), fold_count: z.number().int().nonnegative(),
  accuracy: z.number().min(0).max(1), accuracy_lower_bound: z.number().min(0).max(1),
  brier_score: z.number().min(0).max(1), baseline_brier_score: z.number().min(0).max(1),
  log_loss: z.number().nonnegative(), net_return_pct: z.number(), max_drawdown_pct: z.number().nonnegative(),
  coverage_ratio: z.number().min(0).max(1), max_regime_share: z.number().min(0).max(1),
  passed: z.boolean(), failure_reasons: z.array(z.string()), evaluated_at: z.string(),
})
export const reviewScheduleViewSchema = z.object({
  schema_version: z.literal('review-schedule-view.v1'), review_plan_id: z.string().min(1),
  parent_run_id: z.string().min(1), horizon: z.enum(['30m', '24h', '72h']),
  trigger_type: z.enum(['next_review', 'macro_event', 'invalidation_evidence', 'outcome_due']),
  trigger_ref: z.string().min(1), trigger_at: z.string(),
  status: z.enum(['planned', 'admitted', 'completed', 'skipped', 'failed']),
  child_run_id: z.string().nullable(), failure_code: z.string().nullable(), updated_at: z.string(),
  replacement_run_id: z.string().nullable().optional(),
  replacement_review_plan_id: z.string().nullable().optional(),
  retry_of_plan_id: z.string().nullable().optional(),
  retry_attempt: z.number().int().min(0).max(2).optional(),
})

export const runViewSchema = z.object({
  run_id: z.string(), event_id: z.string(), status: runStatusSchema, strategy_version: z.string(), runtime_version: z.string().default('unknown'),
  snapshot_id: z.string().nullable(), artifact_id: z.string().nullable(), created_at: z.string(),
  updated_at: z.string(), finished_at: z.string().nullable(), latency_ms: z.number().nullable(),
  cost_usd: z.number().nullable(), error_code: z.string().nullable(), headline: z.string().nullable(),
  gate_status: gateStatusSchema.nullable(),
})

export const artifactViewSchema = z.object({
  artifact_id: z.string(), run_id: z.string(), event_id: z.string(), gate_status: gateStatusSchema,
  headline: z.string(), summary: z.string(), facts: z.array(z.string()), inferences: z.array(z.string()),
  counter_thesis: z.string(), uncertainty: z.array(z.string()), transmission_chain: z.array(z.string()),
  citations: z.array(z.string()), forecasts: z.array(forecastSchema),
  gate_decisions: z.array(z.object({ rule_id: z.string(), status: z.string(), reason_code: z.string(), input_hash: z.string() })),
  created_at: z.string(),
})

export const deskSummarySchema = z.object({
  inbox: z.object({ pending_count: z.number(), running_count: z.number(), latest: z.array(runViewSchema) }),
  published_count_30d: z.number(), forecast_count: z.number(), evaluated_count: z.number(),
  health_status: z.string(), active_strategy: z.string(), active_pack: z.string(),
})

export const callViewSchema = z.object({
  call_id: z.string(), run_id: z.string(), role: z.string(), status: z.string(), attempt: z.number(),
  started_at: z.string(), finished_at: z.string().nullable(), latency_ms: z.number().nullable(),
  runtime_id: z.string().nullable(), runtime_version: z.string().nullable(), provider_id: z.string().nullable(),
  model: z.string().nullable(), api_mode: z.string().nullable(), schema_version: z.string().nullable(),
  prompt_tokens: z.number().nullable(), completion_tokens: z.number().nullable(), total_tokens: z.number().nullable(),
  cost_usd: z.number().nullable(), cost_status: z.string(), pricing_version: z.string().nullable(),
  error_code: z.string().nullable(), retryable: z.boolean(),
})

export const stepViewSchema = z.object({
  step_id: z.string(), run_id: z.string(), step_name: z.string(), status: z.string(),
  attempt: z.number(), started_at: z.string(), finished_at: z.string().nullable(),
  latency_ms: z.number().nullable(), error_code: z.string().nullable(),
})

export const runInspectorSchema = z.object({
  run: runViewSchema,
  timeline: z.array(runTimelineItemSchema),
  steps: z.array(stepViewSchema),
  calls: z.array(callViewSchema),
  artifact: artifactViewSchema.nullable(),
  evaluation_count: z.number(),
  evaluations: z.array(z.object({
    evaluation_id: z.string(), forecast_id: z.string(), brier_score: z.number(),
    net_return_pct: z.number(), direction_correct: z.boolean(), label_status: z.string(), evaluated_at: z.string(),
  })),
  snapshot_cutoff_at: z.string().nullable(),
  snapshot_hash: z.string().nullable(),
  evidence_lineage: z.array(evidenceLineageSchema),
  versions: versionLineageSchema,
  orchestration: orchestrationLineageSchema,
}).strict()

export type Forecast = z.infer<typeof forecastSchema>
export type ForecastAuditView = z.infer<typeof forecastAuditViewSchema>
export type StrategyCalibrationView = z.infer<typeof strategyCalibrationViewSchema>
export type ReviewScheduleView = z.infer<typeof reviewScheduleViewSchema>
export type GateStatus = z.infer<typeof gateStatusSchema>
export type RunView = z.infer<typeof runViewSchema>
export type ArtifactView = z.infer<typeof artifactViewSchema>
export type DeskSummary = z.infer<typeof deskSummarySchema>
export type CallView = z.infer<typeof callViewSchema>
export type StepView = z.infer<typeof stepViewSchema>
export type RunInspector = z.infer<typeof runInspectorSchema>
export type SourceManifest = z.infer<typeof sourceManifestSchema>
export type SourceHealth = z.infer<typeof sourceHealthSchema>
export type MarketQuote = z.infer<typeof marketQuoteSchema>
export type MacroEventView = z.infer<typeof macroEventViewSchema>
export type NotificationMessage = z.infer<typeof notificationMessageSchema>
export type ProductHealth = z.infer<typeof productHealthSchema>
export type PilotReadinessCheck = z.infer<typeof pilotReadinessCheckSchema>
export type PilotReadinessReport = z.infer<typeof pilotReadinessReportSchema>
export type {
  ActivePointer,
  CandidateVersion,
  CapabilityManifest,
  EvaluationDataset,
  EvidenceLineage,
  EvolutionOverview,
  Experience,
  ExperienceCreate,
  Experiment,
  ExperimentResult,
  FailurePattern,
  Feedback,
  FeedbackCreate,
  OrchestrationLineage,
  PromotionDecision,
  PromotionDecisionCommand,
  PromotionDecisionResult,
  PromotionGateCheck,
  PromotionMetricDelta,
  PromotionReview,
  PromotionReviewRequest,
  ResearchMemo,
  ResearchMemoCreate,
  ResearchCapabilityInvocation,
  ResearchCapabilityQuery,
  ResearchCapabilityResult,
  ResearchSnapshotManifest,
  RunTimelineItem,
  VersionLineage,
  WorkbenchOverview,
  EvolutionJob,
  EvolutionJobCounts,
  EvolutionJobCreate,
  OperationsOverview,
  RuntimeMode,
  ServiceHeartbeat,
  CandidateProposal,
  CapabilityOperation,
  EvolutionPlanningContext,
  SearchEvidence,
  SearchQuery,
  SearchResult,
  SourceOperation,
  EvolutionCandidateSummary,
  HorizonOutcomeView,
  RequirementReadinessItem,
  ResearchCostBreakdown,
  ResearchCostComponent,
  ResearchInboxItem,
  ResearchInboxView,
  ResearchObservabilityView,
  ResearchValueEvaluation,
  ResearchVersionLineage,
  SourceAttemptView,
} from './generated/r2'

export {
  researchBundlePackRowIdentitySchema,
  researchBundleTaskRowIdentitySchema,
  researchBundleCompiledRowIdentitySchema,
  researchPreparedBundleIdentitySchema,
  researchExecutionMethodDefinitionV2Schema,
  researchExecutionMethodReceiptV2Schema,
  researchExecutionRequestReceiptV2Schema,
  researchExecutionPromptV2Schema,
  researchExecutionPromptIntegrityV2Schema,
} from './generated/r2'

export type {
  ResearchBundlePackRowIdentity,
  ResearchBundleTaskRowIdentity,
  ResearchBundleCompiledRowIdentity,
  ResearchPreparedBundleIdentity,
  ResearchExecutionMethodDefinitionV2,
  ResearchExecutionMethodReceiptV2,
  ResearchExecutionRequestReceiptV2,
  ResearchExecutionPromptV2,
  ResearchExecutionPromptIntegrityV2,
} from './generated/r2'


export {
  researchContextTaskAnchorV1Schema,
  researchContextSelectorIdentityV1Schema,
  researchContextDeclaredPriorRefV1Schema,
  researchContextEvidenceRowDeclarationV1Schema,
  researchContextInputWitnessV1Schema,
  researchContextSelectionRowIdentityV1Schema,
  researchContextExecutionRowIdentityV1Schema,
  researchContextIntegrityRowIdentityV1Schema,
  researchContextSelectionReceiptV1Schema,
  researchContextSelectionExecutionLinkV1Schema,
} from './generated/r2'

export type {
  ResearchContextTaskAnchorV1,
  ResearchContextSelectorIdentityV1,
  ResearchContextDeclaredPriorRefV1,
  ResearchContextEvidenceRowDeclarationV1,
  ResearchContextInputWitnessV1,
  ResearchContextSelectionRowIdentityV1,
  ResearchContextExecutionRowIdentityV1,
  ResearchContextIntegrityRowIdentityV1,
  ResearchContextSelectionReceiptV1,
  ResearchContextSelectionExecutionLinkV1,
} from './generated/r2'


export {
  researchContextSelectorIdentityV2Schema,
  researchContextSelectionReceiptV2Schema,
} from './generated/r2'
export type {
  ResearchContextSelectorIdentityV2,
  ResearchContextSelectionReceiptV2,
} from './generated/r2'


export {
  researchContextGraphRowRefV3Schema,
  researchContextGraphPriorRefV3Schema,
  researchContextSelectionReceiptV3Schema,
} from './generated/r2'
export type {
  ResearchContextGraphRowRefV3,
  ResearchContextGraphPriorRefV3,
  ResearchContextSelectionReceiptV3,
} from './generated/r2'

export {
  researchContextExecutionRowIdentityV3Schema,
  researchContextSelectionExecutionLinkV2Schema,
  researchContextSelectionRowIdentityV3Schema,
  researchExecutionMethodDefinitionV3Schema,
  researchExecutionMethodReceiptV3Schema,
  researchExecutionRequestReceiptV3Schema,
  researchRequiredContextSelectionBindingV1Schema,
} from './generated/r2'
export type {
  ResearchContextExecutionRowIdentityV3,
  ResearchContextSelectionExecutionLinkV2,
  ResearchContextSelectionRowIdentityV3,
  ResearchExecutionMethodDefinitionV3,
  ResearchExecutionMethodReceiptV3,
  ResearchExecutionRequestReceiptV3,
  ResearchRequiredContextSelectionBindingV1,
} from './generated/r2'

export { readCommandReceiptSchema } from './generated/r2'
export type { ReadCommandReceipt } from './generated/r2'

export {
  revisionFeedbackEvaluationSchema,
  revisionFeedbackEvaluationBasisSchema,
  revisionFeedbackEvaluationBindingSchema,
  revisionFeedbackSourceWitnessSchema,
} from './generated/r2'
export type {
  RevisionFeedbackEvaluation,
  RevisionFeedbackEvaluationBasis,
  RevisionFeedbackEvaluationBinding,
  RevisionFeedbackSourceWitness,
} from './generated/r2'

export { harnessComparisonCatalogSchema, harnessComparisonViewSchema, harnessComparisonStartSchema, harnessOwnerSessionPairSchema, harnessOwnerSessionReplySchema, type HarnessComparisonCatalog, type HarnessComparisonView, type HarnessComparisonStart, type HarnessOwnerSessionPair, type HarnessOwnerSessionReply } from "./generated/r2"
export { harnessComparisonCancelSchema, type HarnessComparisonCancel } from "./generated/r2"

export { studyReportComparisonViewSchema, type StudyReportComparisonView } from './generated/r2'
export { notificationEnvelopeV2Schema, type NotificationEnvelopeV2 } from './generated/r2'

export {
  riskScopeSchema, riskScopesViewSchema, riskQuoteViewSchema, riskObservedWindowSchema,
  riskResearchRefSchema, riskMonitorStatusSchema, riskAlertViewSchema, riskSummaryViewSchema,
  riskTimelinePointSchema, riskMissingRangeSchema, riskTimelineViewSchema,
  riskReceiptCommandSchema, riskReceiptViewSchema, riskMonitorCommandSchema,
  riskMonitorCommandReceiptSchema,
} from './generated/r2'
export type {
  RiskScope, RiskScopesView, RiskQuoteView, RiskObservedWindow, RiskResearchRef,
  RiskMonitorStatus, RiskAlertView, RiskSummaryView, RiskTimelinePoint, RiskMissingRange,
  RiskTimelineView, RiskReceiptCommand, RiskReceiptView, RiskMonitorCommand,
  RiskMonitorCommandReceipt,
} from './generated/r2'

export { studyEffectTrackingCommandSchema, studyEffectTrackingPlanRefSchema, studyEffectTrackingMissingWindowSchema, studyEffectTrackingReceiptSchema, studyEffectTrackingRequestStatusSchema, studyEffectTrackingWindowStatusSchema, studyEffectTrackingGroupSchema, studyEffectTrackingStatusSchema } from './generated/r2'
export type { StudyEffectTrackingCommand, StudyEffectTrackingPlanRef, StudyEffectTrackingMissingWindow, StudyEffectTrackingReceipt, StudyEffectTrackingRequestStatus, StudyEffectTrackingWindowStatus, StudyEffectTrackingGroup, StudyEffectTrackingStatus } from './generated/r2'
export { riskResearchCommandSchema, riskResearchRequestSchema, riskResearchStatusSchema, riskResearchOwnerReceiptSchema } from './generated/r2'
export type { RiskResearchCommand, RiskResearchRequest, RiskResearchStatus, RiskResearchOwnerReceipt } from './generated/r2'

export {
  researchProviderCallbackAckSchema, researchProviderCallbackReferenceSchema,
  researchProviderFinishRecordedSchema, researchProviderFinishSubmitSchema,
  researchProviderHostScopeSchema, researchProviderInputRecordedSchema,
  researchProviderInputRefSchema, researchProviderInputSubmitSchema,
  researchProviderToolEmissionRecordedSchema, researchProviderToolEmissionSubmitSchema,
} from './generated/r2'
export type {
  ResearchProviderCallbackAck, ResearchProviderCallbackReference,
  ResearchProviderFinishRecorded, ResearchProviderFinishSubmit, ResearchProviderHostScope,
  ResearchProviderInputRecorded, ResearchProviderInputRef, ResearchProviderInputSubmit,
  ResearchProviderToolEmissionRecorded, ResearchProviderToolEmissionSubmit,
} from './generated/r2'

export { independentReviewIssueV2Schema, independentReviewReceiptV2Schema } from './generated/r2'
export type { IndependentReviewIssueV2, IndependentReviewReceiptV2 } from './generated/r2'

export { researchDecisionSynthesisV1Schema } from './generated/r2'
export type { ResearchDecisionSynthesisV1 } from './generated/r2'

export { researchSkillHypothesisOutputV1Schema } from './generated/r2'

export {
  instrumentContractV1Schema, accountSnapshotV2Schema, positionContextV2Schema, utilityPolicyV2Schema,
  decisionSourceRefV1Schema,
  perpetualNativeToolSchemas,
  decisionSettingsValuesV1Schema, decisionSettingsCommandV1Schema, decisionSettingsRevisionV1Schema,
  decisionSettingsPartitionV1Schema, decisionSettingsCurrentViewV1Schema, decisionSettingsSaveReceiptV1Schema,
  actionComparisonV2Schema, decisionMethodInputsV1Schema,
  decisionActionContextV2Schema, actionCandidateV2Schema, actionCandidateSetV2Schema,
  pendingPublicationTargetV1Schema, publicationBoundTargetV1Schema, publicationAnchorRuleV1Schema,
  publicationAnchorReceiptV1Schema, publicationObservationReceiptV1Schema, frozenForecastV1Schema,
  actionDomainVariableV1Schema, actionComparisonAllowedDomainV1Schema,
  researchDecisionSynthesisV2Schema, actionReviewRequestV2Schema, actionReviewAuthorizationV2Schema,
  actionComparisonDerivationV1Schema, decisionPublicationBindingsV2Schema,
  decisionSnapshotV2Schema, decisionOpportunityV2Schema, decisionMethodInputsV2Schema, actionPlanRevisionV2Schema,
  createPerpetualPaperAccountV1Schema, paperAccountSummaryV1Schema,
  paperAccountCreationReceiptV1Schema, paperAccountListV1Schema,
  perpetualResearchCommandV1Schema, perpetualResearchReceiptV1Schema, perpetualResearchCommandV2Schema, perpetualResearchReceiptV2Schema,
  perpetualResearchCommandV3Schema, perpetualResearchReceiptV3Schema,
  decisionCausalLinkV1Schema, decisionCausalAnalysisV1Schema, decisionReviewOriginalV1Schema, decisionReviewOriginalsV1Schema,
} from './generated/r2'
export type {
  InstrumentContractV1, AccountSnapshotV2, PositionContextV2, UtilityPolicyV2,
  DecisionSourceRefV1,
  DecisionSettingsValuesV1, DecisionSettingsCommandV1, DecisionSettingsRevisionV1,
  DecisionSettingsPartitionV1, DecisionSettingsCurrentViewV1, DecisionSettingsSaveReceiptV1,
  ActionComparisonV2, DecisionMethodInputsV1,
  DecisionActionContextV2, ActionCandidateV2, ActionCandidateSetV2,
  PendingPublicationTargetV1, PublicationBoundTargetV1, PublicationAnchorRuleV1,
  PublicationAnchorReceiptV1, PublicationObservationReceiptV1, FrozenForecastV1,
  ActionDomainVariableV1, ActionComparisonAllowedDomainV1,
  ResearchDecisionSynthesisV2, ActionReviewRequestV2, ActionReviewAuthorizationV2,
  ActionComparisonDerivationV1, DecisionPublicationBindingsV2,
  DecisionSnapshotV2, DecisionOpportunityV2, DecisionMethodInputsV2, ActionPlanRevisionV2,
  CreatePerpetualPaperAccountV1, PaperAccountSummaryV1,
  PaperAccountCreationReceiptV1, PaperAccountListV1,
  PerpetualResearchCommandV1, PerpetualResearchReceiptV1, PerpetualResearchCommandV2, PerpetualResearchReceiptV2,
  PerpetualResearchCommandV3, PerpetualResearchReceiptV3,
  DecisionCausalLinkV1, DecisionCausalAnalysisV1, DecisionReviewOriginalV1, DecisionReviewOriginalsV1,
} from './generated/r2'

export { researchSkillLoadResponseV1Schema, researchSkillLoadResponseV2Schema, researchMethodViewV2Schema } from './generated/r2'
export type { ResearchSkillLoadResponseV1, ResearchSkillLoadResponseV2, ResearchMethodViewV2, ResearchMethodTrialProvenance } from './generated/r2'
export { researchSkillSelectedDeliveryV1Schema, researchSkillSelectedDeliveryLegacyV1Schema, researchSkillSelectedDeliveryResponseV1Schema, researchSkillSelectedProviderInputV1Schema, researchSkillLoadedDetailsV2Schema, researchSkillRecordRequestV2Schema, researchSkillSelectionV3Schema, researchSkillGenerationBindingV2Schema } from './generated/r2'
export type { ResearchSkillSelectedDeliveryV1, ResearchSkillSelectedDeliveryLegacyV1, ResearchSkillSelectedDeliveryResponseV1, ResearchSkillSelectedProviderInputV1, ResearchSkillLoadedDetailsV2, ResearchSkillRecordRequestV2, ResearchSkillSelectionV3, ResearchSkillGenerationBindingV2 } from './generated/r2'

export { perpetualSynthesisDraftV1Schema, perpetualPrepareArgumentsV1Schema, perpetualFinalizeArgumentsV1Schema, perpetualPreparationWitnessV1Schema, perpetualPreparationResultV1Schema, decisionModelAssumptionV1Schema, draftDecisionReferenceV1Schema } from './generated/r2'
export type { PerpetualSynthesisDraftV1, PerpetualPrepareArgumentsV1, PerpetualFinalizeArgumentsV1, PerpetualPreparationWitnessV1, PerpetualPreparationResultV1, DecisionModelAssumptionV1, DraftDecisionReferenceV1 } from './generated/r2'

export { perpetualReviewFindingsV1Schema } from './generated/r2'
export type { PerpetualReviewFindingsV1 } from './generated/r2'
export { perpetualReviewFindingsV2Schema } from './generated/r2'
export type { PerpetualReviewFindingsV2 } from './generated/r2'

export { eventAgendaEntryV1Schema, eventAgendaCoverageV1Schema, eventAgendaSnapshotV1Schema } from './generated/r2'
export type { EventAgendaEntryV1, EventAgendaCoverageV1, EventAgendaSnapshotV1 } from './generated/r2'

export { perpetualPlanWatchCommandV1Schema, perpetualPlanWatchReceiptV1Schema, perpetualPlanReviewTriggerV1Schema } from './generated/r2'
export type { PerpetualPlanWatchCommandV1, PerpetualPlanWatchReceiptV1, PerpetualPlanReviewTriggerV1 } from './generated/r2'

export { perpetualResearchClosureReceiptV1Schema } from './generated/r2'
export type { PerpetualResearchClosureReceiptV1 } from './generated/r2'

export {
  actionReviewAuthorizationV3Schema, actionReviewRequestV3Schema, researchDecisionSynthesisV3Schema,
  perpetualSynthesisDraftV2Schema, perpetualPrepareArgumentsV2Schema,
  perpetualPreparationWitnessV2Schema, perpetualPreparationResultV2Schema,
  eventScenarioActionBranchDraftV1Schema, eventScenarioActionBranchV1Schema,
  eventScenarioActionPlanDraftV1Schema, eventScenarioActionPlanV1Schema,
  eventScenarioApplicabilityDomainV1Schema, eventScenarioAuthoredOriginalV1Schema,
  eventScenarioBranchComparisonV1Schema, eventScenarioBranchReviewScopeV1Schema,
  eventScenarioConditionEvaluationV1Schema, eventScenarioDomainValueV1Schema,
  eventScenarioIdentityV1Schema, eventScenarioManifestOriginalV1Schema,
  eventScenarioMarginalDistributionV1Schema, eventScenarioMetricComparisonV1Schema,
  eventScenarioMetricValueV1Schema, eventScenarioPlanReviewDomainV1Schema,
  eventScenarioPredicateClauseV1Schema, eventScenarioPredicateDefinitionV1Schema,
  eventScenarioPredicateV1Schema, eventScenarioPreparationManifestV1Schema,
  eventScenarioQuoteV1Schema, eventScenarioRealizationV1Schema,
  eventScenarioReviewScopeV1Schema, eventScenarioSelectionInputsV1Schema,
  scenarioBranchSelectionV1Schema,
} from './generated/r2'
export type {
  ActionReviewAuthorizationV3, ActionReviewRequestV3, ResearchDecisionSynthesisV3,
  PerpetualSynthesisDraftV2, PerpetualPrepareArgumentsV2,
  PerpetualPreparationWitnessV2, PerpetualPreparationResultV2,
  EventScenarioActionBranchDraftV1, EventScenarioActionBranchV1,
  EventScenarioActionPlanDraftV1, EventScenarioActionPlanV1,
  EventScenarioApplicabilityDomainV1, EventScenarioAuthoredOriginalV1,
  EventScenarioBranchComparisonV1, EventScenarioBranchReviewScopeV1,
  EventScenarioConditionEvaluationV1, EventScenarioDomainValueV1,
  EventScenarioIdentityV1, EventScenarioManifestOriginalV1,
  EventScenarioMarginalDistributionV1, EventScenarioMetricComparisonV1,
  EventScenarioMetricValueV1, EventScenarioPlanReviewDomainV1,
  EventScenarioPredicateClauseV1, EventScenarioPredicateDefinitionV1,
  EventScenarioPredicateV1, EventScenarioPreparationManifestV1,
  EventScenarioQuoteV1, EventScenarioRealizationV1,
  EventScenarioReviewScopeV1, EventScenarioSelectionInputsV1,
  ScenarioBranchSelectionV1,
} from './generated/r2'
