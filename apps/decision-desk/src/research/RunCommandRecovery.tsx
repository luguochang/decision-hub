import { commandErrorRecord, commandErrorSummary, commandNames } from './commandRequests'
import type { RunCommandState } from './useRunCommand'

export function RunCommandRecovery({ state }: { state: RunCommandState }) {
  const pending = state.pending
  const receipt = state.outcome?.receipt
  return <div className="run-command-recovery" aria-label="任务操作回执">
    {pending && <div className="product-notice" role="status"><strong>有一笔尚未确认的任务操作</strong><p>原操作：{commandNames[pending.intent.command.command]}。原理由：{pending.intent.command.reason}</p><p>任务、用户身份和原请求键保持锁定；返回页面只读取回执，不会自动重新提交。</p>{pending.legacy && <p>这是旧版凭据，用户身份未冻结；可核对原回执，不能直接重发。</p>}<div className="owner-command-actions"><button type="button" className="button secondary" disabled={state.busy} onClick={state.check}>{state.busy ? '正在核对…' : '核对原操作回执'}</button><button type="button" className="button secondary" disabled={state.busy || pending.legacy} onClick={state.retryOriginal}>核对并按原操作重试</button></div><details><summary>查看原操作凭据</summary><pre>{JSON.stringify({ request_id: pending.record.id, intent: pending.intent, legacy: pending.legacy }, null, 2)}</pre></details></div>}
    {receipt && <div role="status" className="product-notice"><p>{receipt.result.status === 'rejected' ? '服务端拒绝了原操作，原理由已保留。请刷新任务状态后核对，未创建新的研究。' : receipt.result.command === 'cancel' ? '停止请求已确认，本任务将停止发布新报告。已发生的调用和费用仍保留，可在研究过程核对。' : receipt.result.command === 'feedback' ? '原反馈已确认保存，供后续复核参考。' : state.viewingConfirmedTarget ? '当前正在查看创建请求已确认的后续研究。研究进度与报告状态以本页任务记录为准，历史资料授权不会自动继承。' : '后续研究已创建，原创建请求已确认；可显式打开新任务。历史资料授权不会自动继承。'}</p>{state.outcome?.cleanupPending && <p>回执已核对，但本地凭据清理未完成；原请求键继续保留，可再次核对。</p>}{receipt.result.status !== 'rejected' && receipt.result.target_run_id && !state.viewingConfirmedTarget && !state.outcome?.cleanupPending && <button className="button secondary" type="button" disabled={state.busy} onClick={state.openConfirmed}>{state.busy ? '正在核对…' : '打开已创建的后续研究'}</button>}<details><summary>查看完整操作回执</summary><pre>{JSON.stringify(receipt, null, 2)}</pre></details></div>}
    {state.error && <div className="command-error" role="alert"><p>{commandErrorSummary(state.error)}</p><details><summary>查看操作错误原始记录</summary><pre>{commandErrorRecord(state.error)}</pre></details></div>}
  </div>
}
