import { acknowledgeRequest, findPendingRequest, pendingRequest } from './pendingRequests'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileText, RefreshCw, ShieldAlert, Upload, XCircle } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { LibraryBlockLocator } from './LibraryBlockLocator'
import { knownTestContentHash, readLibraryPurposes, verifiedTestPurpose, visibleLibraryDocuments } from './libraryPurpose'
import type { DocumentUpload, DocumentUploadReceipt, ResearchDocument, ResearchDocumentVersion } from '../api/types'
import { documentQualityBlocked, libraryResearchRequest } from './libraryRequest'
import { libraryCommands } from './libraryApi'
import { checkedCurrentUploadVersion, checkedUploadReceipt, createUploadIntent, readPendingUpload, requireOriginalUploadFile, uploadErrorSummary, validateUploadFile, type PendingUpload } from './uploadRequests'
import { useSubmission } from './useSubmission'
import { SubmissionError, SubmissionRecovery } from './SubmissionRecovery'

const statusLabel: Record<ResearchDocumentVersion['status'], string> = {
  received: '已接收', parsing: '读取原文中', parsed: '原文已解析', correcting: '修正中', review: '待复核', ready: '可用于研究', failed: '失败', archived: '已归档',
}

type ResearchLibraryPanelProps = {
  routeVersionId?: string
  onOpenVersion?: (versionId?: string) => void
  onUploadStart?: () => void
  onVersionState?: (version: ResearchDocumentVersion) => void
  onVersionRefresh?: (version: ResearchDocumentVersion) => void
  onRequestCorrectionStart?: () => void
  onAcceptCorrectionStart?: () => void
}

export function ResearchLibraryPanel({ routeVersionId, onOpenVersion, onUploadStart, onVersionState, onVersionRefresh, onRequestCorrectionStart, onAcceptCorrectionStart }: ResearchLibraryPanelProps = {}) {
  const queryClient = useQueryClient()
  const uploadInput = useRef<HTMLInputElement>(null)
  const [selectedId, setSelectedId] = useState<string | null>(routeVersionId ?? null)
  const mounted = useRef(false)
  const activeVersionId = useRef<string | null>(selectedId)
  activeVersionId.current = routeVersionId ?? selectedId
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const [blockPage, setBlockPage] = useState({ versionId: selectedId, offset: 0 })
  const [remoteConsentVersion, setRemoteConsentVersion] = useState<string | null>(null)
  useEffect(() => { setRemoteConsentVersion(null) }, [routeVersionId, selectedId])
  useEffect(() => {
    const resetConsent = () => setRemoteConsentVersion(null)
    window.addEventListener('popstate', resetConsent)
    window.addEventListener('hashchange', resetConsent)
    return () => { window.removeEventListener('popstate', resetConsent); window.removeEventListener('hashchange', resetConsent) }
  }, [])
  const [includeTestDocuments, setIncludeTestDocuments] = useState(false)
  const [title, setTitle] = useState('')
  const [market, setMarket] = useState<'crypto_macro' | 'cn_equity' | 'cross_asset'>('crypto_macro')
  const [instrument, setInstrument] = useState('BTC-USDT')
  const [uploadJournal, setUploadJournal] = useState<{ pending: PendingUpload | null; error: Error | null }>(() => {
    try { return { pending: readPendingUpload(), error: null } } catch (error) { return { pending: null, error: error as Error } }
  })
  const [retryFile, setRetryFile] = useState<File | null>(null)
  const [checkingFile, setCheckingFile] = useState(false)
  const [fileError, setFileError] = useState<Error | null>(null)
  const [confirmedUpload, setConfirmedUpload] = useState<{ receipt: DocumentUploadReceipt; version: ResearchDocumentVersion } | null>(null)
  const uploadBusy = useRef(false)
  const pendingUpload = uploadJournal.pending
  const activeUploadRequestId = useRef(pendingUpload?.record.id ?? null)
  const confirmedUploadIds = useRef(new Set<string>())
  const readUpload = async (pending: PendingUpload) => {
    const raw = await api.researchUploadReceipt(pending.record.id)
    if (raw === null) return null
    const receipt = checkedUploadReceipt(raw, pending)
    const current = checkedCurrentUploadVersion(await api.researchDocumentVersion(receipt.version.version_id), receipt)
    return { receipt, version: current }
  }
  const uploadReceiptQuery = useQuery({ queryKey: ['research-library-upload-receipt', pendingUpload?.record.id], queryFn: () => readUpload(pendingUpload!), enabled: Boolean(pendingUpload), retry: false, refetchOnWindowFocus: false })
  const confirmUpload = (result: { receipt: DocumentUploadReceipt; version: ResearchDocumentVersion }) => {
    confirmedUploadIds.current.add(result.receipt.request_id)
    acknowledgeRequest('document-upload', result.receipt.request_id)
    queryClient.setQueryData(['research-library-version', result.version.version_id], result.version)
    void queryClient.invalidateQueries({ queryKey: ['research-library-documents'] })
    if (mounted.current && activeUploadRequestId.current === result.receipt.request_id) {
      setConfirmedUpload(result)
      setFileError(null)
      try { setUploadJournal({ pending: readPendingUpload(), error: null }) } catch (error) { setUploadJournal({ pending: null, error: error as Error }) }
      setRetryFile(null)
    }
  }
  useEffect(() => { if (uploadReceiptQuery.data) confirmUpload(uploadReceiptQuery.data) }, [uploadReceiptQuery.data])
  const documentsQuery = useQuery({ queryKey: ['research-library-documents'], queryFn: api.researchDocuments, refetchInterval: 5_000, retry: false })
  const versionQuery = useQuery({ queryKey: ['research-library-version', selectedId], queryFn: () => api.researchDocumentVersion(selectedId!), enabled: Boolean(selectedId), refetchInterval: 3_000, retry: false })
  const blocksQuery = useQuery({ queryKey: ['research-library-blocks', selectedId, versionQuery.data?.parse_receipt_ref], queryFn: () => api.researchDocumentBlocks(selectedId!), enabled: Boolean(selectedId && versionQuery.data?.parse_receipt_ref), retry: false })
  const correctionQuery = useQuery({ queryKey: ['research-library-correction', selectedId], queryFn: () => api.researchDocumentCorrection(selectedId!), enabled: Boolean(selectedId), refetchInterval: 3_000, retry: false })
  const claimsQuery = useQuery({ queryKey: ['research-library-claims', selectedId], queryFn: () => api.researchClaims(selectedId!), enabled: Boolean(selectedId), refetchInterval: 5_000, retry: false })
  const documents = documentsQuery.data ?? []
  const purposeVersionIds = [...new Set([selectedId, ...documents.map(item => item.latest_version_id)].filter((id): id is string => Boolean(id)))].slice(0, 100)
  const purposesQuery = useQuery({ queryKey: ['research-library-content-purpose', purposeVersionIds], queryFn: ({ signal }) => readLibraryPurposes(purposeVersionIds, signal), enabled: purposeVersionIds.length > 0, retry: false })
  const purposes = purposesQuery.isError ? [] : (purposesQuery.data ?? [])
  const visibleDocuments = visibleLibraryDocuments(documents, purposes, includeTestDocuments)
  const hiddenTests = documents.filter(document => verifiedTestPurpose(document, purposes)).length
  const version = versionQuery.data?.version_id === selectedId ? versionQuery.data : undefined
  const purpose = purposes.find(item => item.version_id === version?.version_id && item.document_id === version?.document_id && item.original_file_hash === version?.original_file_hash)
  const isFixture = version?.original_file_hash === knownTestContentHash || purpose?.classification === 'known_test_content'
  const purposeLoading = purposesQuery.isPending || purposesQuery.isFetching
  const purposeUnconfirmed = purposeLoading || purposesQuery.isError || !purpose
  const qualityUsageText = isFixture ? '测试资料仅供回看，不能用于新研究' : purposeUnconfirmed ? '资料用途尚未核验，解析质量不代表研究使用资格' : '解析质量通过；研究使用仍需核对适用范围与授权'
  const readingOriginal = version?.status === 'received' || version?.status === 'parsing'
  const qualityBlocked = !readingOriginal && (documentQualityBlocked(version) || ['blocked_low_quality', 'failed'].includes(correctionQuery.data?.quality_status ?? ''))
  const blocks = blocksQuery.data ?? []
  const blockOffset = blockPage.versionId === selectedId ? Math.min(blockPage.offset, Math.max(0, Math.ceil(blocks.length / 25) - 1) * 25) : 0
  const selectedDocument = useMemo(() => documents.find((item) => item.document_id === version?.document_id), [documents, selectedId, version?.document_id])
  const researchSelection = version?.original_file_hash === knownTestContentHash
    ? { request: null, reason: '本版原件哈希属于已登记的虚构验收内容，不能作为新研究的市场证据。可继续查看全部原文与处理记录。' }
    : libraryResearchRequest(selectedDocument, version, remoteConsentVersion === selectedId)
  const upload = useMutation({
    retry: false,
    mutationFn: async (variables: { file: File; metadata?: DocumentUpload; pending?: PendingUpload; requestId?: string; startVersion: string | null; startHash: string }) => {
      validateUploadFile(variables.file)
      const policy = await api.researchUploadPolicy()
      if (variables.file.size > policy.max_file_size_bytes) throw new Error('所选文件超过上传限制，请缩小文件后重新选择。')
      let pending = variables.pending
      if (pending) {
        await requireOriginalUploadFile(variables.file, pending.intent)
        const existing = await readUpload(pending)
        if (existing) return existing
      } else {
        if (readPendingUpload()) throw new Error('还有尚未确认的上传，请先核对原请求。')
        const intent = await createUploadIntent(variables.file, variables.metadata!)
        pending = { record: pendingRequest('document-upload', intent), intent }
        variables.requestId = pending.record.id
        activeUploadRequestId.current = pending.record.id
        if (mounted.current) setUploadJournal({ pending, error: null })
      }
      const receipt = checkedUploadReceipt(await api.uploadResearchDocumentReceipt(variables.file, pending.intent, pending.record.id), pending)
      return { receipt, version: checkedCurrentUploadVersion(await api.researchDocumentVersion(receipt.version.version_id), receipt) }
    },
    onSuccess: (result, variables) => {
      confirmUpload(result)
      if (mounted.current && activeUploadRequestId.current === result.receipt.request_id && activeVersionId.current === variables.startVersion && window.location.hash === variables.startHash) {
        setSelectedId(result.version.version_id)
        if (onVersionState) onVersionState(result.version); else onOpenVersion?.(result.version.version_id)
      }
    },
    onError: (_error, variables) => { if (mounted.current && !confirmedUploadIds.current.has(variables.requestId ?? '')) setRetryFile(null) },
    onSettled: () => { uploadBusy.current = false },
  })
  const selectUploadFile = async (file: File) => {
    if (uploadBusy.current || uploadJournal.error) return
    uploadBusy.current = true
    setFileError(null)
    setConfirmedUpload(null)
    activeUploadRequestId.current = pendingUpload?.record.id ?? null
    upload.reset()
    if (pendingUpload) {
      setCheckingFile(true)
      setRetryFile(null)
      try { await requireOriginalUploadFile(file, pendingUpload.intent); if (mounted.current) setRetryFile(file) }
      catch (error) { if (mounted.current) setFileError(error as Error) }
      finally { uploadBusy.current = false; if (mounted.current) setCheckingFile(false) }
      return
    }
    const metadata: DocumentUpload = { schema_version: 'document-upload.v1', title: title.trim() || file.name, source_type: 'private_file', source_profile_id: null, author: null, published_at: null, market_scopes: [market], instrument_scopes: instrument.trim() ? [instrument.trim()] : [], privacy_mode: 'local_only', visibility: 'private', supersedes_version_id: null, notes: null }
    onUploadStart?.()
    upload.mutate({ file, metadata, startVersion: activeVersionId.current, startHash: window.location.hash })
  }
  const retryUpload = () => {
    if (!pendingUpload || !retryFile || uploadBusy.current || uploadReceiptQuery.isFetching) return
    uploadBusy.current = true
    upload.mutate({ file: retryFile, pending: pendingUpload, requestId: pendingUpload.record.id, startVersion: activeVersionId.current, startHash: window.location.hash })
  }
  const uploadLocked = Boolean(pendingUpload || uploadJournal.error || upload.isPending || checkingFile)
  const uploadError = uploadJournal.error ?? fileError ?? (upload.isError && !confirmedUploadIds.current.has(upload.variables?.requestId ?? '') ? upload.error : null) ?? (uploadReceiptQuery.isError ? uploadReceiptQuery.error : null)
  const uploadUnconfirmed = Boolean(pendingUpload || (upload.variables?.requestId && !confirmedUploadIds.current.has(upload.variables.requestId)))
  const openSavedUpload = useMutation({
    retry: false,
    mutationFn: async (variables: { receipt: DocumentUploadReceipt; startVersion: string | null; startHash: string }) => checkedCurrentUploadVersion(await api.researchDocumentVersion(variables.receipt.version.version_id), variables.receipt),
    onSuccess: (current, variables) => {
      queryClient.setQueryData(['research-library-version', current.version_id], current)
      if (mounted.current && activeUploadRequestId.current === variables.receipt.request_id && activeVersionId.current === variables.startVersion && window.location.hash === variables.startHash) {
        setConfirmedUpload({ receipt: variables.receipt, version: current })
        setSelectedId(current.version_id)
        if (onVersionState) onVersionState(current); else onOpenVersion?.(current.version_id)
      }
    },
  })
  const requestCorrection = useMutation({
    mutationFn: async (versionId: string) => {
      const next = await api.requestResearchCorrection(versionId)
      if (next.version_id !== versionId) throw new Error('资料整理回执与所选版本不匹配，尚未确认结果。')
      return next
    },
    onSuccess: (nextVersion, versionId) => {
      if (mounted.current && activeVersionId.current === versionId) onVersionState?.(nextVersion)
      void queryClient.invalidateQueries({ queryKey: ['research-library-version', versionId] })
      void queryClient.invalidateQueries({ queryKey: ['research-library-correction', versionId] })
    },
  })
  const acceptCorrection = useMutation({
    mutationFn: async (versionId: string) => {
      const next = await api.acceptResearchCorrection(versionId)
      if (next.version_id !== versionId) throw new Error('整理确认回执与所选版本不匹配，尚未确认结果。')
      return next
    },
    onSuccess: (nextVersion, versionId) => {
      if (mounted.current && activeVersionId.current === versionId) onVersionState?.(nextVersion)
      void queryClient.invalidateQueries({ queryKey: ['research-library-version', versionId] })
      void queryClient.invalidateQueries({ queryKey: ['research-library-claims', versionId] })
    },
  })
  const startResearch = useSubmission('library-research', researchSelection.request,
    JSON.stringify([routeVersionId, selectedId, version?.version_id, remoteConsentVersion]),
    (original, key) => api.submitResearchWithLibrary(original as Parameters<typeof api.submitResearchWithLibrary>[0], key),
    result => { window.location.hash = `/queued?run=${encodeURIComponent(result.run_id)}` })
  const recover = useMutation({
    mutationFn: async (variables: { versionId: string; originalFileHash: string }) => {
      const record = pendingRequest('document-recovery', variables)
      const result = await libraryCommands.recover(variables.versionId, variables.originalFileHash, record.id)
      acknowledgeRequest('document-recovery', record.id)
      return result
    },
    onSuccess: (next, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['research-library-documents'] })
      if (mounted.current && activeVersionId.current === variables.versionId) { setSelectedId(next.version_id); onVersionState?.(next) }
    },
  })
  const reviewClaim = useMutation({
    mutationFn: async ({ id, decision }: { versionId: string; id: string; decision: 'accept' | 'reject' }) => {
      const payload = claimReviewPayload(id, decision)
      const record = pendingRequest('claim-review', payload)
      const result = await libraryCommands.reviewClaim(id, decision, payload.reason, record.id)
      acknowledgeRequest('claim-review', record.id)
      return result
    },
    onSuccess: (claim, variables) => {
      queryClient.setQueryData(['research-library-claims', variables.versionId], (rows: Awaited<ReturnType<typeof api.researchClaims>> | undefined) => rows?.map(item => item.claim_id === claim.claim_id ? claim : item))
      void queryClient.invalidateQueries({ queryKey: ['research-library-claims', variables.versionId] })
    },
  })
  useEffect(() => {
    if (routeVersionId) setSelectedId(routeVersionId)
  }, [routeVersionId])
  useEffect(() => { if (version && (!routeVersionId || version.version_id === routeVersionId)) onVersionRefresh?.(version) }, [version, routeVersionId, onVersionRefresh])
  const stepIndex = !version ? 0 : qualityBlocked ? 1 : version.status === 'failed' ? version.correction_status === 'failed' ? 2 : 1 : version.status === 'ready' ? 4 : version.status === 'review' || version.status === 'correcting' ? 2 : version.status === 'parsed' ? 2 : version.status === 'parsing' ? 1 : 0
  const documentSteps = ['上传', '解析', '人工审核', '质量复检', version?.original_file_hash === knownTestContentHash ? '已归档测试内容' : '可用于研究']

  return <div className="content-wrap research-library-page">
    <section className="page-heading">
      <div><div className="eyebrow"><span className="live-dot" />已归档资料</div><h1>资料</h1><p>上传后核对原文、解析结果与关键数值。审核通过的版本可被选入 BTC 研究；是否成为报告依据仍由证据审核决定。</p></div>
      <button type="button" className="button primary" disabled={upload.isPending || checkingFile || Boolean(uploadJournal.error)} onClick={() => uploadInput.current?.click()}><Upload size={14} />{upload.isPending ? '上传中…' : checkingFile ? '核对文件中…' : pendingUpload ? '重新选择原文件' : '上传资料'}</button><input ref={uploadInput} aria-label="选择研究资料文件" type="file" hidden accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.html" onChange={(event) => { const file = event.target.files?.[0]; if (file) void selectUploadFile(file); event.currentTarget.value = '' }} />
    </section>
    <ol className="research-library-stepper" aria-label="资料处理进度">{documentSteps.map((step, index) => <li key={step} className={`${index < stepIndex ? 'complete' : ''} ${index === stepIndex ? 'current' : ''}`}><span>{index + 1}</span><strong>{step}</strong></li>)}</ol>
    <p>支持 PDF、图片和文本；单个文件最多 20 MiB（20,971,520 字节）。</p>
    {uploadError && <div className="error-callout" role="alert"><ShieldAlert size={15} /><div><strong>{uploadUnconfirmed ? '上传尚未确认' : '未发送上传'}</strong><p>{!uploadUnconfirmed && uploadErrorSummary(uploadError).startsWith('尚未确认上传结果') ? '尚未发送上传请求。请核对文件与上传设置后重试。' : uploadErrorSummary(uploadError)}</p>{!uploadUnconfirmed && <p>尚未发送新请求。</p>}<details><summary>查看错误详情</summary><p>{uploadError.message}</p></details></div></div>}
    {pendingUpload && <section className="panel library-section" aria-label="尚未确认的上传"><h2>核对原上传</h2><p>原文件：{pendingUpload.intent.file_name} · {pendingUpload.intent.file_size_bytes.toLocaleString('en-US')} 字节。标题与适用范围已锁定。返回此页只查询回执，不会自动上传。</p><p role="status">{uploadReceiptQuery.isFetching ? '正在核对原请求回执…' : uploadReceiptQuery.data === null ? '暂未找到原请求回执。请重新选择完全相同的原文件，再明确点击重试。' : '上传结果尚未确认，请先核对回执。'}</p><div className="library-actions"><button className="button secondary" disabled={upload.isPending || uploadReceiptQuery.isFetching} onClick={() => void uploadReceiptQuery.refetch()}>核对上传回执</button><button className="button primary" disabled={!retryFile || upload.isPending || checkingFile || uploadReceiptQuery.isFetching} onClick={retryUpload}>按原内容重试上传</button></div>{retryFile && <p>原文件内容已核对。重试前会再次查询回执，仅在明确没有回执时发送同一请求。</p>}<details><summary>原上传凭据</summary><pre>{JSON.stringify({ request_id: pendingUpload.record.id, intent: pendingUpload.intent }, null, 2)}</pre></details></section>}
    {confirmedUpload && <section className="panel library-section" aria-label="已确认上传"><h2>{confirmedUpload.receipt.deduplicated ? '已复用相同资料' : '原件已保存'}</h2><p>已核对原请求和文件内容。核对时状态：{statusLabel[confirmedUpload.version.status]}。{confirmedUpload.version.status === 'ready' ? '资料已完成处理，可打开后核对适用范围与本次研究的资料使用授权。' : confirmedUpload.version.status === 'failed' ? '资料处理未完成，原件与当前处理记录已保留；请打开资料查看原因。' : confirmedUpload.version.status === 'archived' ? '资料已归档，请打开后核对当前版本与可用范围。' : '仍需完成资料处理与审核后才可用于研究。'}</p><button className="button secondary" disabled={openSavedUpload.isPending} onClick={() => openSavedUpload.mutate({ receipt: confirmedUpload.receipt, startVersion: activeVersionId.current, startHash: window.location.hash })}>{openSavedUpload.isPending ? '正在读取最新状态…' : '打开已保存资料'}</button>{openSavedUpload.isError && openSavedUpload.variables?.receipt.request_id === confirmedUpload.receipt.request_id && <LibraryReadError title="已保存资料暂时无法打开" error={openSavedUpload.error.message} retry={() => openSavedUpload.mutate({ receipt: confirmedUpload.receipt, startVersion: activeVersionId.current, startHash: window.location.hash })} />}<details><summary>上传回执原始记录</summary><pre>{JSON.stringify(confirmedUpload.receipt, null, 2)}</pre></details></section>}
    {documentsQuery.isError && <LibraryReadError title="资料列表暂时不可用" error={documentsQuery.error.message} retry={() => void documentsQuery.refetch()} />}
    <SubmissionRecovery state={startResearch} />
    {startResearch.pending.length > 0 && <p role="status">有一笔资料研究尚未确认。原版本与资料使用授权已保留在提交凭据中；返回页面不会自动创建研究或勾选 AI 授权。重新核对同一版本与本次授权后，才可按原内容明确重试。</p>}
    <div className="library-workspace">
      <details className="panel library-picker" key={selectedId ?? 'choose-document'} open={!selectedId}>
        <summary>{selectedId ? '切换资料 / 设置上传范围' : '选择资料 / 设置上传范围'}<span>{documents.length} 份已归档资料</span></summary>
        <header className="panel-header"><div><h2>资料版本</h2><p>原件和各次处理结果均会保留，可随时回看。</p></div><button className="text-button" onClick={() => void queryClient.invalidateQueries({ queryKey: ['research-library-documents'] })}><RefreshCw size={13} />刷新</button></header>
        <details className="library-metadata"><summary>资料标题与适用范围（默认 BTC / 宏观）</summary><div className="library-form"><input disabled={uploadLocked} value={pendingUpload?.intent.metadata.title ?? title} onChange={(event) => setTitle(event.target.value)} placeholder="留空使用文件名" aria-label="资料标题" /><select disabled={uploadLocked} value={pendingUpload?.intent.metadata.market_scopes[0] ?? market} onChange={(event) => setMarket(event.target.value as typeof market)} aria-label="资料适用领域"><option value="crypto_macro">BTC 与宏观</option><option value="cn_equity">中国权益</option><option value="cross_asset">跨资产</option></select><input disabled={uploadLocked} value={pendingUpload?.intent.metadata.instrument_scopes.join(' · ') ?? instrument} onChange={(event) => setInstrument(event.target.value)} placeholder="适用标的" aria-label="适用标的" /></div></details>
        <label><input type="checkbox" checked={includeTestDocuments} onChange={event => setIncludeTestDocuments(event.target.checked)} />显示已核验的测试资料（{hiddenTests} 份）</label>{purposesQuery.isError && <p role="status">资料用途暂未核验，所有资料仍可见。测试内容在研究准入处仍受校验。<button className="text-button" onClick={() => void purposesQuery.refetch()}>重新核对用途</button></p>}
        <div className="library-list">{documentsQuery.isLoading ? <div className="workspace-state"><RefreshCw className="spin" size={15} />正在读取资料列表…</div> : documentsQuery.isError ? null : documents.length === 0 ? <div className="empty-line">还没有资料。上传 PDF、图片或文本后，会在这里看到解析状态。</div> : visibleDocuments.length === 0 ? <p role="status">当前列表都是已核验的测试资料，可勾选上方选项查看原件。</p> : visibleDocuments.map((document) => <DocumentRow key={document.document_id} document={document} isTest={verifiedTestPurpose(document, purposes)} selected={document.latest_version_id === selectedId || (!selectedId && document === selectedDocument)} onClick={() => { setSelectedId(document.latest_version_id); onOpenVersion?.(document.latest_version_id ?? undefined) }} />)}</div>
      </details>
      <section className="panel library-detail">
        <header className="panel-header"><div><h2>{selectedDocument?.title ?? (selectedId && versionQuery.isPending ? '正在读取资料' : '选择一份资料')}</h2><p>原始内容、质量信号、修正差异、观点候选均可复核。</p></div>{version && <span className={`status-pill ${version.original_file_hash === knownTestContentHash ? 'warning' : qualityBlocked ? 'danger' : version.status === 'ready' ? 'success' : version.status === 'failed' ? 'danger' : 'warning'}`}><span className="status-dot" />{version.original_file_hash === knownTestContentHash ? '测试内容 · 不作为市场证据' : version.status === 'failed' ? '失败' : qualityBlocked ? '质量未通过' : statusLabel[version.status]}</span>}</header>
        {versionQuery.isError ? <LibraryReadError title="这份资料版本暂时不可用" error={versionQuery.error.message} retry={() => void versionQuery.refetch()} /> : !version ? <div className="empty-line" role={selectedId && versionQuery.isPending ? 'status' : undefined}>{selectedId && versionQuery.isPending ? '正在读取原文与处理结果…' : '上传或选择资料后查看完整处理链路。'}</div> : <>
          {version.status === 'failed' && <div className="error-callout" role="alert"><ShieldAlert size={15} /><div><strong>{version.correction_status === 'failed' ? '资料整理未完成' : '原文解析未完成'}</strong><p>原版本与归档原件已保留。重新处理会建立关联的新版本，从原文重新解析；审核通过后才可用于研究。</p>{selectedDocument?.latest_version_id && selectedDocument.latest_version_id !== version.version_id && !findPendingRequest('document-recovery', { versionId: version.version_id, originalFileHash: version.original_file_hash }) ? <button className="button secondary" onClick={() => { setSelectedId(selectedDocument.latest_version_id); onOpenVersion?.(selectedDocument.latest_version_id ?? undefined) }}>查看最新处理版本</button> : <button className="button secondary" disabled={recover.isPending} onClick={() => recover.mutate({ versionId: version.version_id, originalFileHash: version.original_file_hash })}>{recover.isPending ? '正在登记重新处理…' : '重新处理这份资料'}</button>}{findPendingRequest('document-recovery', { versionId: version.version_id, originalFileHash: version.original_file_hash }) && <p role="status">上次重新处理尚未确认；点击按钮将核对同一请求。</p>}</div></div>}
          {recover.isError && recover.variables?.versionId === selectedId && <LibraryReadError title="重新处理尚未确认" error={recover.error.message} retry={() => { void versionQuery.refetch(); void documentsQuery.refetch() }} />}
          <div className="library-facts"><Fact label="适用范围" value={selectedDocument?.market_scopes.map((scope) => scopeLabel[scope] ?? '其他范围').join(' · ') || '正在读取'} /><Fact label="原文解析" value={version.parse_receipt_ref ? '已生成解析回执' : '等待解析'} /><Fact label="质量" value={version.parse_receipt_ref ? qualityLabel(version.quality.quality_status) : '尚未检查'} /><Fact label="观点候选" value={`${version.claim_count}`} /></div><div className="library-original-link"><a href={api.researchDocumentOriginalUrl(version.version_id)} target="_blank" rel="noreferrer">打开归档原件</a>{version.supersedes_version_id && <button className="text-button" onClick={() => { setSelectedId(version.supersedes_version_id); onOpenVersion?.(version.supersedes_version_id ?? undefined) }}>查看上次处理记录</button>}<details className="library-version-audit"><summary>版本与处理凭据</summary><p>资料版本：<code>{version.version_id}</code></p><p>解析回执：<code>{version.parse_receipt_ref ?? '尚未生成'}</code></p></details></div>
          {readingOriginal && <p role="status">{version.status === 'received' ? '原件已接收，等待读取原文。' : '正在读取原文，请稍候。'}解析质量尚未检查，暂不能整理或加入研究。</p>}
          {qualityBlocked && !(version.status === 'failed' && !version.parse_receipt_ref) && <div className="error-callout" role="alert"><ShieldAlert size={15} /><div><strong>这份资料的正文尚不可用</strong><p>当前版本不能整理或加入研究。请先打开归档原件核对，再上传含文字的 PDF 或文本；扫描 PDF 和图片需要可用的本地 OCR 才能识别正文。</p><p>原件与当前处理记录会保留；重新上传的可读资料需重新审核。</p></div></div>}
          {version.status === 'ready' && !qualityBlocked && <section className="library-section library-research-entry" aria-label="研究使用"><h3>用于新研究</h3><SubmissionError error={startResearch.error} />{version.status === 'ready' && (researchSelection.request ? <div className="library-actions">{selectedDocument?.privacy_mode === 'local_only' && <label className="library-research-consent"><input type="checkbox" disabled={startResearch.busy} checked={remoteConsentVersion === selectedId} onChange={(event) => setRemoteConsentVersion(event.target.checked ? selectedId : null)} />允许本次研究将这份资料的片段与候选观点发送给已配置的 AI 服务。原件仍保留本地。</label>}<button className="button primary" disabled={startResearch.busy || startResearch.blocked || (selectedDocument?.privacy_mode === 'local_only' && remoteConsentVersion !== selectedId)} onClick={startResearch.execute}>{startResearch.busy ? '正在创建研究任务…' : '用这份资料发起 BTC 研究'}</button><span>当前提供 30 分钟、24 小时、72 小时三个研究窗口。资料先进入候选范围，报告是否引用仍由证据审核决定。</span></div> : <div className="error-callout"><ShieldAlert size={15} /><span>{researchSelection.reason}</span></div>)}</section>}
          <section className="library-section"><h3>解析质量</h3><Quality quality={version.quality} evaluated={Boolean(version.parse_receipt_ref)} status={version.status} /><div className="library-actions">{version.status === 'parsed' && !qualityBlocked && <button className="button primary" onClick={() => { onRequestCorrectionStart?.(); requestCorrection.mutate(version.version_id) }} disabled={requestCorrection.isPending}>请求资料整理建议</button>}{version.status === 'review' && !qualityBlocked && <button className="button primary" onClick={() => { onAcceptCorrectionStart?.(); acceptCorrection.mutate(version.version_id) }} disabled={acceptCorrection.isPending}>确认整理并提取候选</button>}{version.status === 'ready' && !qualityBlocked && <span className={`status-pill ${isFixture || purposeUnconfirmed ? 'warning' : 'success'}`}><CheckCircle2 size={12} />{qualityUsageText}</span>}</div></section>
          {blocksQuery.isError && <LibraryReadError title="原始解析内容暂时不可用" error={blocksQuery.error.message} retry={() => void blocksQuery.refetch()} />}
          <LibraryBlockLocator key={version.version_id} blocks={blocks} versionId={version.version_id} onLocate={index => setBlockPage({ versionId: version.version_id, offset: Math.floor(index / 25) * 25 })} /><section className="library-section" aria-label="原始解析内容"><h3>原始解析块{blocksQuery.data ? `（${blocks.length}）` : ''}</h3>{!version.parse_receipt_ref ? <p>解析完成后可在这里逐段核对原文。</p> : blocksQuery.isPending ? <p role="status">正在读取解析内容…</p> : !blocksQuery.isError && <><div className="library-actions"><button className="text-button" disabled={blockOffset === 0} onClick={() => setBlockPage({ versionId: selectedId, offset: blockOffset - 25 })}>上一页原文</button><span>{blocks.length ? `${blockOffset + 1}–${Math.min(blockOffset + 25, blocks.length)} / 共 ${blocks.length} 段` : '解析未返回文本段，请核对原件与质量提示。'}</span><button className="text-button" disabled={blockOffset + 25 >= blocks.length} onClick={() => setBlockPage({ versionId: selectedId, offset: blockOffset + 25 })}>下一页原文</button></div><div className="block-list">{blocks.slice(blockOffset, blockOffset + 25).map((block, index) => <article className={block.needs_review ? 'document-block needs-review' : 'document-block'} key={block.block_id}><span>第 {blockOffset + index + 1} 段 · 第 {block.page_no} 页 · 识别置信度 {Math.round(block.confidence * 100)}%</span><p>{block.content}</p></article>)}</div></>}</section>
          {requestCorrection.isError && requestCorrection.variables === selectedId && <div className="error-callout" role="alert"><ShieldAlert size={15} /><span>请求语义修正失败：{requestCorrection.error.message}</span></div>}
          {acceptCorrection.isError && acceptCorrection.variables === selectedId && <div className="error-callout" role="alert"><ShieldAlert size={15} /><span>确认修正失败：{acceptCorrection.error.message}</span></div>}
          {correctionQuery.isError && <LibraryReadError title="资料整理记录暂时不可用" error={correctionQuery.error.message} retry={() => void correctionQuery.refetch()} />}
          {correctionQuery.data && <section className="library-section"><h3>资料整理建议</h3><p>{correctionQuery.data.correction_model_ref === 'financial-correction.deterministic.v1' ? '本版采用规则规范化，只处理格式与可确定的文本问题，不代表模型理解或事实核验。' : '本版包含模型修正建议，需逐条核对原文与数字后确认。'}</p><details><summary>处理版本审计</summary><code>{correctionQuery.data.correction_model_ref}</code></details>{correctionQuery.data.unresolved_fragments.length > 0 && <div className="error-callout"><ShieldAlert size={15} /><span>仍有未解析片段，确认前需要人工判断。</span></div>}<div className="diff-list">{correctionQuery.data.changes.map((change) => <article key={change.change_id}><div className="diff-original">原文：{change.original}</div><div className="diff-corrected">修正：{change.corrected}</div><small>{correctionReason(change.reason)} · {change.requires_user_confirmation ? '需要确认' : '低风险'}</small><details><summary>核对整理原因记录</summary><code>{change.reason}</code></details></article>)}</div></section>}
          {claimsQuery.isError && <LibraryReadError title="观点候选暂时不可用" error={claimsQuery.error.message} retry={() => void claimsQuery.refetch()} />}
          {reviewClaim.isError && reviewClaim.variables?.versionId === selectedId && <LibraryReadError title="观点审核结果尚未确认" error={reviewClaim.error.message} retry={() => void claimsQuery.refetch()} />}
          <section className="library-section"><h3>观点候选 · 需要独立证据</h3><p className="product-muted">当前内置提取器使用规则识别句子。候选可能遗漏或误判，接受候选也不代表其事实已经核实。</p>{claimsQuery.isPending ? <p role="status">正在读取观点候选…</p> : claimsQuery.isError ? null : (claimsQuery.data ?? []).length === 0 ? <div className="empty-line">{version.original_file_hash === knownTestContentHash ? '测试内容保留供验收回看，不能选入新市场研究。' : version.status === 'ready' ? '本次未识别出观点候选。资料仍可选入研究，候选数量不代表资料价值或事实可信度。' : '尚未提取观点；只有确认整理后才会进入候选层。'}</div> : <div className="claim-list">{claimsQuery.data?.map((claim) => { const unconfirmed = unconfirmedClaimDecision(claim.claim_id); return <article key={claim.claim_id}><div><strong>{stanceLabel[claim.stance] ?? '方向未识别'}</strong><span>{scopeLabel[claim.market_scope] ?? '其他范围'} · {claimStatusLabel[claim.status] ?? '待核对'}</span><div className="claim-actions">{unconfirmed ? <button className="text-button" disabled={reviewClaim.isPending} onClick={() => reviewClaim.mutate({ versionId: version.version_id, id: claim.claim_id, decision: unconfirmed })}>核对上次{unconfirmed === 'accept' ? '接受' : '拒绝'}结果</button> : claim.status === 'candidate' && <><button className="text-button" disabled={reviewClaim.isPending} onClick={() => reviewClaim.mutate({ versionId: version.version_id, id: claim.claim_id, decision: 'accept' })}>接受</button><button className="text-button danger-text" disabled={reviewClaim.isPending} onClick={() => reviewClaim.mutate({ versionId: version.version_id, id: claim.claim_id, decision: 'reject' })}>拒绝</button></>}</div></div><p>{claim.claim_text}</p><small>{claim.supporting_quote}</small></article> })}</div>}</section>
        </>}
      </section>
    </div>
  </div>
}

const scopeLabel: Record<string, string> = { crypto_macro: 'BTC 与宏观', cn_equity: '中国权益', cross_asset: '跨资产' }
const stanceLabel: Record<string, string> = { bullish: '偏多观点', bearish: '偏空观点', neutral: '中性观点', mixed: '方向分歧', unknown: '方向未识别' }
const claimStatusLabel: Record<string, string> = { candidate: '待审核', accepted: '已接受', rejected: '已拒绝', superseded: '已被新版本替代' }
function claimReviewPayload(claimId: string, decision: 'accept' | 'reject') { return { claimId, decision, reason: decision === 'accept' ? 'owner_confirmed' : 'owner_rejected' } }
function unconfirmedClaimDecision(claimId: string) { return (['accept', 'reject'] as const).find(decision => Boolean(findPendingRequest('claim-review', claimReviewPayload(claimId, decision)))) }
function LibraryReadError({ title, error, retry }: { title: string; error: string; retry: () => void }) { return <div className="error-callout" role="alert"><ShieldAlert size={15} /><div><strong>{title}</strong><p>当前结果尚未确认，请重新读取这部分记录。</p><button className="button secondary" onClick={retry}>重新读取记录</button><details><summary>查看错误详情</summary><p>{error}</p></details></div></div> }

function DocumentRow({ document, isTest, selected, onClick }: { document: ResearchDocument; isTest: boolean; selected: boolean; onClick: () => void }) { const scopeName: Record<string, string> = { crypto_macro: 'BTC 与宏观', cn_equity: '中国权益', cross_asset: '跨资产' }; return <button className={selected ? 'library-row selected' : 'library-row'} onClick={onClick}><FileText size={16} /><span><strong>{document.title}</strong>{isTest && <small>测试资料 · 不作为市场证据</small>}<small>{document.market_scopes.map((scope) => scopeName[scope] ?? '其他范围').join(' · ')} · {document.version_count} 个版本</small></span><small>{document.latest_version_id ? '查看' : '—'}</small></button> }
function Fact({ label, value }: { label: string; value: string }) { return <div className="detail-cell"><span>{label}</span><strong title={value}>{value}</strong></div> }
function Quality({ quality, evaluated, status }: { quality: ResearchDocumentVersion['quality']; evaluated: boolean; status: ResearchDocumentVersion['status'] }) { if (!evaluated) return <div className="quality-card"><RefreshCw size={16} /><div><strong>尚未进行解析质量检查</strong><span>原文解析完成后显示检查结果。</span>{quality.issues.length > 0 && <details><summary>{status === 'failed' ? '查看处理失败记录' : '查看处理记录'}</summary><ul>{quality.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></details>}</div></div>; const blocked = quality.quality_status === 'blocked_low_quality' || quality.quality_status === 'failed'; return <div className={blocked ? 'quality-card blocked' : 'quality-card'}>{blocked ? <XCircle size={16} /> : <CheckCircle2 size={16} />}<div><strong>{qualityLabel(quality.quality_status)}</strong><span>文本 {Math.round(quality.text_readability * 100)}% · 定位 {Math.round(quality.locator_completeness * 100)}% · 数字疑点 {quality.numeric_suspicion}</span><small>{quality.issues.length ? `有 ${quality.issues.length} 项质量提示，请核对原文。` : '没有质量问题'}</small>{quality.issues.length > 0 && <details><summary>查看质量检查明细</summary><ul>{quality.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></details>}</div></div> }

function qualityLabel(value: string) { return ({ ready_for_correction: '解析质量通过', needs_review: '需要人工核对', blocked_low_quality: '需要核对原文', failed: '质量检查失败', pending: '等待质量检查' } as Record<string, string>)[value] ?? '查看质量明细' }

function correctionReason(reason: string) { return reason === 'unicode_and_whitespace_normalization' ? '格式与空白规范化' : '整理建议，请核对原文' }
