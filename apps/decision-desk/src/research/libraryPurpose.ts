import { z } from 'zod'
import { request } from '../api/client'
import type { ResearchDocument } from '../api/types'

export const knownTestContentHash = '18148ad136409c15ed833adc91cc9d1694dd16d8f4abd0d391bba2268f908808'
const purposeSchema = z.object({
  schema_version: z.literal('document-content-purpose.v1'), document_id: z.string().min(1), version_id: z.string().min(1),
  original_file_hash: z.string().regex(/^[a-f0-9]{64}$/), classification: z.enum(['known_test_content', 'unverified', 'original_unverified']),
  original_verified: z.boolean(), known_content_id: z.literal('automated-acceptance-fiction.v1').nullable(), historical_import_provenance: z.literal('unknown'),
}).strict().superRefine((value, context) => {
  const known = value.classification === 'known_test_content'
  if (known !== value.original_verified || known !== (value.known_content_id !== null) || (known && value.original_file_hash !== knownTestContentHash)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '资料用途与原件核验不一致。' })
  }
})
export type LibraryPurpose = z.infer<typeof purposeSchema>
export async function readLibraryPurposes(versionIds: string[], signal: AbortSignal): Promise<LibraryPurpose[]> {
  if (versionIds.length > 100 || new Set(versionIds).size !== versionIds.length) throw new Error('资料用途查询范围无效。')
  // Small serial groups avoid opening 100 concurrent file/DB readers.
  const result: LibraryPurpose[] = []
  for (let offset = 0; offset < versionIds.length; offset += 4) {
    if (signal.aborted) throw signal.reason
    result.push(...await Promise.all(versionIds.slice(offset, offset + 4).map(async id => {
      const value = purposeSchema.parse(await request(`/v1/research-library/versions/${encodeURIComponent(id)}/provenance`, { signal }))
      if (value.version_id !== id) throw new Error('资料用途与版本不匹配。')
      return value
    })))
  }
  return result
}
export function verifiedTestPurpose(document: Pick<ResearchDocument, 'document_id' | 'latest_version_id'>, purposes: LibraryPurpose[]): boolean {
  return purposes.some(item => item.document_id === document.document_id && item.version_id === document.latest_version_id
    && item.original_file_hash === knownTestContentHash && item.classification === 'known_test_content' && item.original_verified)
}
export function visibleLibraryDocuments<T extends Pick<ResearchDocument, 'document_id' | 'latest_version_id'>>(documents: T[], purposes: LibraryPurpose[], includeTests: boolean): T[] {
  return includeTests ? documents : documents.filter(document => !verifiedTestPurpose(document, purposes))
}
