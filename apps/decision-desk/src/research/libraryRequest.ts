import type { ResearchDocument, ResearchDocumentVersion, ResearchRequest } from '../api/types'

export function documentQualityBlocked(version: ResearchDocumentVersion | undefined): boolean {
  return version?.quality?.quality_status === 'blocked_low_quality' || version?.quality?.quality_status === 'failed'
}

export function libraryResearchRequest(
  document: ResearchDocument | undefined,
  version: ResearchDocumentVersion | undefined,
  allowRemoteContent = false,
): { request: ResearchRequest; reason: null } | { request: null; reason: string } {
  if (!document || !version || version.document_id !== document.document_id) {
    return { request: null, reason: '请先选择可读取的资料版本。' }
  }
  if (version.status !== 'ready') {
    return { request: null, reason: '这份资料尚未完成审核，不能加入研究。' }
  }
  if (documentQualityBlocked(version)) {
    return { request: null, reason: '这份资料的正文尚不可用，请先核对原件并上传含文字的资料。' }
  }
  if (!document.market_scopes.includes('crypto_macro')) {
    return { request: null, reason: '这份资料不属于当前已开通的 BTC / 宏观研究范围。' }
  }
  if (document.instrument_scopes.length > 0 && !document.instrument_scopes.includes('BTC-USDT')) {
    return { request: null, reason: '这份资料限定了其他标的，不能作为 BTC 研究资料。' }
  }
  return {
    request: {
      schema_version: 'research-request.v1',
      research_family: 'crypto_macro',
      instrument_scope: ['BTC-USDT'],
      focus: '结合这份已审核资料和最新市场证据，复核 BTC 在 30 分钟、24 小时和 72 小时三个窗口的主要判断、反证与适用边界。',
      horizon: '72h',
      library_context_mode: 'selected',
      allow_remote_library_content: allowRemoteContent,
      selected_version_ids: [version.version_id],
    },
    reason: null,
  }
}
