import calendarLabels from '../../../../contracts/presentation/calendar-labels.json'
import type { MacroEventView } from '../api/types'

export function calendarDay(event: MacroEventView): string {
  return event.scheduled_date ?? event.scheduled_at?.slice(0, 10) ?? ''
}

export function calendarStatus(event: MacroEventView): string {
  if (event.invalidated_at) return '旧解析记录已撤回'
  return { scheduled: '已计划', released: '已发布', revised: '已修订', cancelled: '官方已取消' }[event.status]
}

export function calendarTime(event: MacroEventView): string {
  const day = calendarDay(event)
  if (event.time_precision === 'date') return `${day || '日期待确认'} · 具体时间待公布`
  if (event.time_precision !== 'exact') return `${day || '计划时间'} · 时间精度待核实`
  if (!event.scheduled_at || !/(Z|[+-]\d{2}:\d{2})$/.test(event.scheduled_at)) return '发布时间待核实'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(event.scheduled_at))
}

export function calendarInNextDays(event: MacroEventView, days: number, now = Date.now()): boolean {
  const day = calendarDay(event)
  if (!day) return false
  const distance = Date.parse(`${day}T00:00:00Z`) - Date.parse(new Date(now).toISOString().slice(0, 10))
  return distance >= 0 && distance <= days * 86_400_000
}

const eventNames: Record<string, string> = calendarLabels.families
export function eventName(family: string) { return eventNames[family.toLowerCase()] ?? (/[\u3400-\u9fff]/.test(family) ? family : '宏观事件发布') }

export function calendarSource(source: string): string {
  return (calendarLabels.sources as Record<string, string>)[source] ?? '其他来源'
}
export function calendarAuthority(authority: string): string {
  return ({ official: '官方来源', primary: '一手来源', secondary: '转述来源', unknown: '来源待核实' } as Record<string, string>)[authority] ?? '来源待核实'
}
