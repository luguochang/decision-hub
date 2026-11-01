export function sourceFailure(code?: string | null, httpStatus?: number | null): { reason: string; recovery: string } {
  if (code === 'source_access_denied' || httpStatus === 403) return {
    reason: '来源拒绝访问',
    recovery: '当前无法从该来源更新内容。请核对官方访问要求与采集授权；恢复访问后再核对新采集记录。',
  }
  if (code === 'source_authentication_required' || httpStatus === 401) return {
    reason: '来源要求身份验证',
    recovery: '请由维护人员核对该来源的访问凭据和授权，再查看新的采集结果。',
  }
  if (code === 'source_rate_limited' || httpStatus === 429) return { reason: '来源请求过于频繁', recovery: '等待下次计划采集；若持续出现，请核对来源的请求配额。' }
  if (code === 'source_timeout') return { reason: '等待来源响应超时', recovery: '请核对网络和来源响应，查看下次计划采集结果。' }
  if (code === 'source_parse_error') return { reason: '来源内容无法解析', recovery: '已收到的内容不能用于更新事件。请维护人员核对页面结构与解析规则。' }
  return { reason: '来源暂时无法采集', recovery: '请查看本次请求与原始响应，核对来源连接及采集配置。' }
}
