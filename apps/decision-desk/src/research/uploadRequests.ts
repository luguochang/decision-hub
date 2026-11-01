import { documentUploadIntentSchema, documentUploadPolicySchema, documentUploadReceiptSchema, researchDocumentVersionSchema } from '../api/schemas'
import type { DocumentUpload, DocumentUploadIntent, DocumentUploadReceipt, ResearchDocumentVersion } from '../api/types'
import { listPendingRequests, type PendingRequest } from './pendingRequests'

export const documentUploadOwner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
export const uploadPolicy = documentUploadPolicySchema.parse({ schema_version: 'document-upload-policy.v1', max_file_size_bytes: 20_971_520 })
export type PendingUpload = { record: PendingRequest; intent: DocumentUploadIntent }

export function validateUploadFile(file: File): void {
  if (file.size === 0) throw new Error('所选文件没有内容，请选择有内容的资料后重新上传。')
  if (file.size > uploadPolicy.max_file_size_bytes) throw new Error('所选文件超过 20 MiB（20,971,520 字节），请缩小文件后重新选择。')
}

async function fileBytes(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === 'function') return file.arrayBuffer()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => reader.result instanceof ArrayBuffer ? resolve(reader.result) : reject(new Error('无法读取所选文件，请重新选择。'))
    reader.onerror = () => reject(new Error('无法读取所选文件，请重新选择。'))
    reader.readAsArrayBuffer(file)
  })
}

export async function uploadFileHash(file: File): Promise<string> {
  validateUploadFile(file)
  if (!crypto.subtle) throw new Error('当前浏览器无法核对文件内容，尚未发送上传请求。请使用支持安全文件校验的浏览器。')
  const digest = await crypto.subtle.digest('SHA-256', await fileBytes(file))
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
}

export async function createUploadIntent(file: File, metadata: DocumentUpload): Promise<DocumentUploadIntent> {
  return documentUploadIntentSchema.parse({ schema_version: 'document-upload-intent.v1', owner_id: documentUploadOwner,
    file_name: file.name, file_size_bytes: file.size, file_sha256: await uploadFileHash(file), metadata })
}

export function readPendingUpload(): PendingUpload | null {
  const records = listPendingRequests('document-upload')
  if (records.length > 1) throw new Error('发现多份尚未确认的上传凭据，请保留会话并核对原始记录，暂不能发送新上传。')
  if (!records.length) return null
  const parsed = documentUploadIntentSchema.safeParse(records[0].payload)
  if (!parsed.success || parsed.data.owner_id !== documentUploadOwner || parsed.data.file_size_bytes <= 0 || parsed.data.file_size_bytes > uploadPolicy.max_file_size_bytes) {
    throw new Error('尚未确认的上传凭据与当前用户或文件限制不一致，暂不能发送新上传。')
  }
  return { record: records[0], intent: parsed.data }
}

function comparable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(comparable).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${comparable(item)}`).join(',')}}`
  return JSON.stringify(value)
}

export function checkedUploadReceipt(value: unknown, pending: PendingUpload): DocumentUploadReceipt {
  const parsed = documentUploadReceiptSchema.safeParse(value)
  if (!parsed.success || parsed.data.request_id !== pending.record.id || comparable(parsed.data.intent) !== comparable(pending.intent) ||
    parsed.data.version.original_file_hash !== pending.intent.file_sha256 || parsed.data.version.published_at !== pending.intent.metadata.published_at ||
    parsed.data.version.supersedes_version_id !== pending.intent.metadata.supersedes_version_id) {
    throw new Error('上传回执与原请求不一致，尚未确认结果。请保留原提交凭据。')
  }
  return parsed.data
}

export function checkedCurrentUploadVersion(value: unknown, receipt: DocumentUploadReceipt): ResearchDocumentVersion {
  const parsed = researchDocumentVersionSchema.safeParse(value)
  if (!parsed.success || parsed.data.version_id !== receipt.version.version_id || parsed.data.document_id !== receipt.version.document_id ||
    parsed.data.original_file_hash !== receipt.intent.file_sha256) throw new Error('当前资料版本与上传回执不一致，尚未确认结果。')
  return parsed.data
}

export async function requireOriginalUploadFile(file: File, intent: DocumentUploadIntent): Promise<void> {
  validateUploadFile(file)
  if (file.name !== intent.file_name || file.size !== intent.file_size_bytes || await uploadFileHash(file) !== intent.file_sha256) {
    throw new Error('所选文件与原上传不一致。请重新选择同名、同大小且内容完全相同的原文件。')
  }
}

export function uploadErrorSummary(error: Error): string {
  const localPrefixes = ['所选文件', '当前浏览器无法核对', '无法读取所选文件', '无法保存本次提交凭据', '无法读取尚未确认的提交凭据', '尚未确认的上传凭据与当前用户', '发现多份尚未确认的上传凭据', '上传回执与原请求不一致', '当前资料版本与上传回执不一致', '还有尚未确认的上传', '请求超时，尚未确认结果', '网络连接中断，尚未确认结果']
  if (localPrefixes.some(prefix => error.message.startsWith(prefix))) return error.message
  if (error.message === 'document_file_too_large') return '文件超过上传限制，请核对文件大小。原请求已保留。'
  if (error.message === 'document_empty_file') return '所选文件没有内容，请重新选择有内容的资料。'
  return '尚未确认上传结果。原请求已保留；请先核对回执，再按原内容重试。'
}
