import { useRef, useState } from 'react'
import { useModalScrollLock } from './useModalScrollLock'
import { ArrowUpRight, X } from 'lucide-react'
import { api, type ObservationReceipt } from '../api/client'
import { restoredSubmissionText } from '../research/submissionReceipts'
import { useSubmission } from '../research/useSubmission'
import { SubmissionError, SubmissionRecovery } from '../research/SubmissionRecovery'

export function ObservationSubmitDialog({ onClose, onReceipt, originHash }: { onClose: () => void; onReceipt: (receipt: ObservationReceipt, originHash: string) => void; originHash?: string }) {
  useModalScrollLock()
  const openedAt = useRef(originHash ?? window.location.hash).current
  const [text, setText] = useState(() => restoredSubmissionText('observation-submit') ?? '')
  const payload = { text: text.trim() }
  const submission = useSubmission('observation-submit', payload, openedAt,
    (original, key) => api.submitObservation((original as typeof payload).text, key),
    (receipt, _original, sourceHash) => onReceipt(receipt, sourceHash))
  const unconfirmed = submission.originalPending
  return <div className="modal-backdrop" onClick={() => { if (!submission.busy) onClose() }}>
    <section className="submit-modal" role="dialog" aria-modal="true" aria-labelledby="submit-title" onClick={event => event.stopPropagation()}>
      <div className="drawer-header"><div><span className="eyebrow">事件文本 · 规则分析</span><h2 id="submit-title">提交一段事件文本</h2></div><button className="icon-button" disabled={submission.busy} onClick={onClose} aria-label="关闭"><X size={17} /></button></div>
      <p className="modal-copy">正文会保存为事件输入，按接收时点冻结证据，再交给规则分析流程处理。接收回执后会打开对应执行记录；需要 AI 主动取证时，可在账本选择“发起研究”。</p>
      {unconfirmed && !submission.busy && <p className="product-notice" role="status">已恢复尚未确认的提交。核对文本后可重试读取原回执，系统不会自动发送。</p>}
      <textarea value={text} maxLength={200000} disabled={submission.busy} onChange={event => setText(event.target.value)} placeholder="粘贴讲话、公告或新闻正文…" aria-label="事件文本" autoFocus />
      <SubmissionError error={submission.error} />
      <SubmissionRecovery state={submission} legacy />
      <div className="modal-footer"><span className="char-count">{text.length.toLocaleString()} / 200,000</span><button className="button secondary" disabled={submission.busy} onClick={onClose}>取消</button><button className="button primary" disabled={!payload.text || submission.busy || submission.blocked} onClick={submission.execute}>{submission.busy ? '正在核对接收结果…' : unconfirmed ? '重试核对原回执' : '提交并查看记录'} <ArrowUpRight size={15} /></button></div>
    </section>
  </div>
}
