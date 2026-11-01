/** UI semantic view proposal only. Not a verified backend schema.
 * Map actual Hub Query View fields in an adapter; do not POST these types directly.
 * See docs/API_Contract_Map.md for verified path list and missing contracts.
 */
export type HealthState = 'online' | 'unknown' | 'degraded';
export type RunState = 'queued' | 'researching' | 'completed' | 'failed';
export type ReportKind = 'research_only' | 'synthesis_incomplete';
export type ProbabilityState = 'uncalibrated' | 'calibrated' | 'unknown';
export type TradePermission = 'unauthorized' | 'authorized' | 'unknown';
export interface RecoverableError {
  code: string;
  origin: string;
  retryable: boolean | null;
  message: string;
}
export interface RunView {
  id: string;
  title: string;
  status: RunState;
  parentRunId: string | null;
  createdAt: string;
  updatedAt: string | null;
  reportKind: ReportKind | null;
  evidenceCount: number;
  sourceCount: number;
  coveredRequirementCount: number | null;
  requiredCount: number | null;
  probabilityState: ProbabilityState;
  tradePermission: TradePermission;
  error: RecoverableError | null;
  referencedDocumentVersions: Array<{documentId: string; versionId: string}>;
}
export interface EvidenceView {
  id: string;
  title: string;
  sourceName: string;
  sourceUrl: string | null;
  observedAt: string | null;
  requirementIds: string[];
  authority: string | null;
  quality: string | null;
  publicSummary: string;
}
export interface MacroEventView {
  id: string;
  name: string;
  releaseAt: string | null;
  timeZone: string;
  previous: number | null;
  forecast: number | null;
  actual: number | null;
  surprise: number | null;
  unit: string | null;
  sourceState: HealthState;
  observedAt: string | null;
  versions: Array<{id: string; observedAt: string; actual: number | null}>;
}
export interface DocumentVersionView {
  documentId: string;
  versionId: string;
  originalVersionId: string;
  state: 'uploading' | 'parsing' | 'review' | 'quality_insufficient' |
    'confirmed' | 'ready' | 'linked' | 'failed';
  blocks: Array<{id: string; original: string; proposal: string | null;
    decision: 'pending' | 'accepted' | 'rejected'; risk: string | null}>;
  hardGapCount: number;
  qualityGate: 'pending' | 'passed' | 'failed';
  linkedRunIds: string[];
}
