import type { SubmissionState } from './useSubmission'
import { submissionRefusalCodes } from './submissionReceipts'

export function SubmissionError({ error }: { error: Error | null }) {
  if (!error) return null
  const explanation = error.message.startsWith('无法保存本次提交凭据') || error.message.startsWith('本标签页的提交凭据') || error.message.startsWith('接收回执与本标签页已有请求绑定不一致') ? error.message
    : error.message === 'research_submission_legacy_binding_unavailable' ? '旧提交凭据缺少完整绑定，重复同一请求不能补齐。原任务或报告可能已存在，凭据已保留；请按原内容与接收时间手动核对任务记录。'
      : error.message === 'research_submission_intent_conflict' ? '这份提交凭据与原提交内容不一致。原任务可能已存在，凭据已保留；请先按原内容与接收时间核对任务记录。'
        : error.message === 'research_submission_receipt_corrupt' ? '原接收回执无法完整核对。原任务可能已存在，凭据已保留；请先核对原任务与原始回执，暂不重复提交。'
    : error.message === 'observation_idempotency_conflict' ? '本次提交凭据与已有记录不一致，尚未确认新分析。请保留文本并核对已有记录。'
      : '尚未确认提交结果，原提交凭据已保留。请稍后按原内容重试核对。'
  return <div role="alert"><p className="form-error">提交未确认：{explanation}</p>{submissionRefusalCodes.has(error.message) && <a className="product-link" href="#/runs">手动核对研究任务列表</a>}<details><summary>查看提交错误原始记录</summary><pre>{error.message}</pre></details></div>
}

export function SubmissionRecovery({ state, legacy = false }: { state: SubmissionState; legacy?: boolean }) {
  return <>
    {state.conflicts.map(conflict => <section className="product-notice" aria-label="提交凭据冲突" key={conflict.record.request.id}>
      <p role="alert"><strong>提交凭据发生冲突</strong>。{conflict.explanation}</p>
      <p>收到的响应仅保留用于核对，未自动归属或打开任务。原本地记录保留，请先核对提交内容与原任务记录。</p>
      <details><summary>查看冲突与原始记录</summary><pre>{JSON.stringify(conflict, null, 2)}</pre></details>
    </section>)}
    {state.pending.length > 0 && <details><summary>尚未确认的提交原始凭据</summary><pre>{JSON.stringify(state.pending, null, 2)}</pre></details>}
    {state.outcomes.map(outcome => <section className="product-notice" aria-label="已收到的提交回执" key={outcome.record.request.id}>
      <p role="status"><strong>收到接收回执</strong>。{legacy ? '事件输入已接收，执行结果需打开记录核对。' : '研究任务已接收，接收时为排队状态；当前进度与报告是否生成需打开任务核对。'}</p>
      {!outcome.persisted && <p role="alert">本地凭据核对或回执保存未完成，原提交凭据未清理。本标签页可打开该任务；刷新或关闭后不能保证保留这份回执。</p>}
      {outcome.persisted && outcome.cleanupPending && <p role="status">接收回执已保存，但原本地凭据尚未清理。不会自动重发，请先打开原记录。</p>}
      <button className="button secondary" disabled={state.busy} onClick={() => state.open(outcome)}>{legacy ? '打开已接收的执行记录' : '打开已接收的研究任务'}</button>
      <details><summary>提交与接收回执原始记录</summary><p>请求键与提交内容由本标签页关联；服务回执未提供完整原意图校验。</p><pre>{JSON.stringify(outcome.record, null, 2)}</pre></details>
    </section>)}
  </>
}
