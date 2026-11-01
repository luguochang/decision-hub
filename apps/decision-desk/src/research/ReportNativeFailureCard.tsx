import type { ResearchRunDetailView } from '../api/types'
import type { Ref } from 'react'
import { assetTime } from '../product/assetFormat'
import { projectResearchProgress } from '../product/researchProgressProjection'
import { failureExplanation } from './technicalPresentation'

const causeLabel = (code: string) => code === 'skill_callback_transport_timeout'
  ? '技能回调传输等待超时' : '原生研究执行未完成'

/** A display of authenticated API originals; it authorizes no research command. */
export function ReportNativeFailureCard({ detail, currentStateUnconfirmed = false, problemFocusRef }: {
  detail: ResearchRunDetailView
  currentStateUnconfirmed?: boolean
  problemFocusRef?: Ref<HTMLElement>
}) {
  const view = projectResearchProgress(detail, !currentStateUnconfirmed)
  const failure = view.firstNativeFailure ?? view.currentNativeFailure
  const terminalFailure = ['failed', 'rejected', 'cancelled'].includes(detail.run.status)
  if (!failure && !view.nativeStop && !(terminalFailure && detail.run.failure)) return null
  const first = Boolean(view.firstNativeFailure)
  const coverage = view.failureCoverage
  const closing = detail.run.failure
  const closingText = closing ? failureExplanation(closing.error_code, closing.cause_code ?? null) : null
  return <section ref={problemFocusRef} tabIndex={-1} className="research-notice warning research-native-failure" aria-label="已认证原生失败原因" role="status">
    <div>
      <h3>{failure ? first ? '最早已认证的原生阻断' : '当前已认证的原生阻断' : '原生失败原因待核对'}</h3>
      {currentStateUnconfirmed && <p>当前连接未确认，下方保留最近一次读取的原始回执；请刷新核对最新状态。</p>}
      {failure && <>
        <p>{causeLabel(failure.error_code)}。</p>
        <p>实际发生：<time dateTime={failure.occurred_at}>{assetTime(failure.occurred_at)}</time>；原回执入库：<time dateTime={failure.recorded_at}>{assetTime(failure.recorded_at)}</time>。</p>
      </>}
      {coverage?.status !== 'complete' && <p>最早原因尚未查全。{coverage?.uncovered_generations.length
        ? `第 ${coverage.uncovered_generations.join('、')} 轮的原生终态绑定尚未认证。`
        : '尚无可完整认证的原生终态原件。'}当前记录不能替代缺失的历史首因。</p>}
      {coverage?.status === 'complete' && <p>{first
        ? '本次原生终态历史已完整核对，可确认以上为最早已认证的原生失败。'
        : coverage.earliest_status === 'no_failure' && !failure
          ? '本次已认证的原生终态没有失败原因；任务最终收尾需单独核对。'
          : '覆盖记录标记已完成，但当前还无法确认最早原生原因，仍保持未知。'}</p>}
      {failure?.error_code === 'skill_callback_transport_timeout' && <p>这是技能请求的传输等待边界；它不能单独证明后台作业已停止。合法研究超过一分钟仍可继续，原任务截止时间与取消规则分别核对。</p>}
      {closing && <p>最终收尾单独保留：{closingText ?? '另有任务结束错误，可在问题详情中核对。'}它不会覆盖上面的原生阻断。</p>}
      <details><summary>核对已认证原始回执</summary>
        {failure ? <dl>
          <div><dt>认证原因码</dt><dd>{failure.error_code}</dd></div>
          <div><dt>研究代次</dt><dd>{failure.generation}</dd></div>
          <div><dt>原回执</dt><dd>{failure.original_output_id}</dd></div>
          <div><dt>原回执内容身份</dt><dd>{failure.original_payload_hash}</dd></div>
        </dl> : <p>没有可认证原件，保留为未知；整体终态错误码不能推断出具体子因。</p>}
        {closing && <dl><div><dt>最终任务错误码</dt><dd>{closing.error_code}</dd></div><div><dt>最终收尾原因码</dt><dd>{closing.cause_code ?? '未提供'}</dd></div></dl>}
      </details>
    </div>
  </section>
}
