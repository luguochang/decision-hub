import { researchDocumentVersionSchema } from '../api/schemas'
import type { DocumentUpload, DocumentUploadIntent, DocumentUploadReceipt } from '../api/types'

export const uploadMetadata: DocumentUpload = { schema_version: 'document-upload.v1', title: '原始宏观资料', source_type: 'private_file', source_profile_id: null, author: null, published_at: null, market_scopes: ['crypto_macro'], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only', visibility: 'private', supersedes_version_id: null, notes: null }
export const uploadStamp = '2026-10-01T06:00:00Z'
export const uploadVersion = researchDocumentVersionSchema.parse({ schema_version: 'research-document-version.v1', version_id: 'docver_upload', document_id: 'doc_upload', original_file_ref: 'document://original', original_file_hash: 'a'.repeat(64), status: 'received', correction_status: 'not_requested', published_at: null, observed_at: uploadStamp, received_at: uploadStamp, created_at: uploadStamp, supersedes_version_id: null, parse_receipt_ref: null, corrected_content_ref: null, block_count: 0, claim_count: 0, quality: { layout_coverage: 0, text_readability: 0, table_integrity: 0, image_coverage: 0, numeric_suspicion: 0, encoding_errors: 0, locator_completeness: 0, quality_status: 'blocked_low_quality', issues: ['not_parsed'] } })
export function uploadReceipt(intent: DocumentUploadIntent, requestId: string, deduplicated = false): DocumentUploadReceipt {
  return { schema_version: 'document-upload-receipt.v1', request_id: requestId, intent, version: { ...uploadVersion, original_file_hash: intent.file_sha256, published_at: intent.metadata.published_at, supersedes_version_id: intent.metadata.supersedes_version_id }, deduplicated, created_at: uploadStamp }
}
