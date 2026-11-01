import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { forecastAlarmsApi, type ForecastAlarmReceipt, type ForecastAlarmView } from '../api/forecastAlarms'
import './risk.css'

const time = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))

function ForecastAlarmCard({ item }: { item: ForecastAlarmView }) {
  const node = useRef<HTMLElement>(null)
  const [exposure, setExposure] = useState<ForecastAlarmReceipt | null>(null)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const [acknowledged, setAcknowledged] = useState(item.acknowledged)
  const [ackBusy, setAckBusy] = useState(false)
  const [ackFailed, setAckFailed] = useState(false)
  const alarm = item.alarm

  useEffect(() => {
    let mounted = true, intersecting = false, pending = false, completed = false
    let display: ForecastAlarmReceipt | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    const clear = () => { if (timer) clearTimeout(timer); timer = undefined }
    const visible = () => intersecting && document.visibilityState === 'visible'
    const track = async () => {
      if (!visible() || pending || completed) return
      pending = true
      try {
        display ??= await forecastAlarmsApi.display(alarm)
        if (!mounted) return
        setFailed(false)
        if (visible()) {
          // Report only one conservative second after continuous foreground visibility.
          // This is browser-reported evidence, not full-window reading or outcome benefit.
          clear()
          timer = setTimeout(() => {
            timer = undefined
            if (!mounted || !visible() || !display) return
            pending = true
            void forecastAlarmsApi.exposure(alarm, display).then(receipt => {
              if (mounted) { completed = true; setExposure(receipt); setFailed(false) }
            }).catch(() => { if (mounted) setFailed(true) }).finally(() => { pending = false })
          }, 1250)
        }
      } catch { if (mounted) setFailed(true) }
      finally { pending = false }
    }
    const update = () => { if (!visible()) clear(); else void track() }
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      intersecting = Boolean(entries[0]?.isIntersecting && entries[0].intersectionRatio >= .5)
      update()
    }, { threshold: [.5] })
    if (node.current) observer?.observe(node.current)
    document.addEventListener('visibilitychange', update)
    return () => { mounted = false; clear(); observer?.disconnect(); document.removeEventListener('visibilitychange', update) }
  }, [alarm, retry])

  const acknowledge = async () => {
    if (!exposure || ackBusy) return
    setAckBusy(true)
    try { await forecastAlarmsApi.acknowledge(alarm, exposure); setAcknowledged(true); setAckFailed(false) }
    catch { setAckFailed(true) }
    finally { setAckBusy(false) }
  }
  return <section ref={node} className="risk-monitor-row" aria-label="实验预警">
    <div>
      <h3>{alarm.method === 'BASE_EMPIRICAL_V1' ? '固定基线' : '波动条件候选'} · {alarm.direction === 'long' ? '持多下行风险' : '持空上行风险'}</h3>
      <p>未来固定一天窗口内，不利幅度可能达到 {Number(alarm.threshold) * 100}%。样本频率尚未校准，不能视为交易胜率。</p>
      <p className="product-muted">提前记录：{time(alarm.known_at)}；观察窗口：{time(alarm.window_start_at)}—{time(alarm.window_end_at)}。</p>
      <p className="product-muted">{exposure || item.exposure_status === 'visible' ? '已保存浏览器报告的短时可见回执。' : '仅有系统预警；当前浏览器可见回执尚未确认。'} 不代表持续阅读或避险收益。</p>
      {failed && <p className="product-notice danger" role="alert">显示回执未确认。<button className="button secondary" onClick={() => setRetry(value => value + 1)}>重试原回执</button></p>}
      {ackFailed && <p className="product-notice danger" role="alert">知悉回执未确认，请用原按钮重试。</p>}
      <button className="button secondary" disabled={!exposure || acknowledged || ackBusy} onClick={() => void acknowledge()}>{acknowledged ? '已知悉' : ackBusy ? '正在保存知悉回执' : '我已知悉'}</button>
      <details className="risk-audit"><summary>预测与回执原件</summary><p>预警：{alarm.alarm_id}；预测：{alarm.prediction_id}；试验：{alarm.trial_id}。</p><p>历史样本频率：{(Number(alarm.frequency) * 100).toFixed(1)}%，属于实验值；没有自动交易权限。</p></details>
    </div>
  </section>
}

export function ForecastAlarmPanel({ scopeId }: { scopeId: string }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['forecast-alarms', scopeId], queryFn: ({ signal }) => forecastAlarmsApi.list(scopeId, signal), enabled: open, retry: false })
  return <details className="product-card" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><strong>实验预警记录</strong><span className="product-muted"> · 事前预测与可见回执</span></summary>
    {open && <div>
      <p className="product-muted">基线与候选独立保存事前预警。实际价格变化提醒另有记录；本区尚未证明预测优势。</p>
      <button className="button secondary" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新实验预警</button>
      {query.error && <p className="product-notice danger" role="alert">实验预警原件暂时无法核对，请刷新重试。已有记录仅供参照。</p>}
      {query.isPending && <p>正在读取已保存的事前预警…</p>}
      {query.data?.items.length === 0 && <p>当前没有满足准入条件的事前预警。样本不足、未按时发布或未达到固定门槛都会保留排除原因。</p>}
      {query.data?.items.map(item => <ForecastAlarmCard key={item.alarm.alarm_id} item={item} />)}
    </div>}
  </details>
}
