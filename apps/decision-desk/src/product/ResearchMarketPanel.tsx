import { useQuery } from '@tanstack/react-query'
import { assets } from '../api/assets'
import { AssetError } from './assetFormat'

const reasons: Record<string, string> = {
  gold_provider_not_verified: '黄金专属报价来源尚未接入并核验',
  gold_calendar_not_verified: '报价时段、休市与延迟规则尚未核验',
  gold_data_rights_not_verified: '历史数据使用范围尚未确认',
  gold_outcome_not_verified: '黄金后续观察与评估尚未接通',
  gold_domain_pack_not_implemented: '黄金研究规则与独立证据要求尚未实现',
  spot_research_disabled: '通过现货 AI 研究的正式准入',
  spot_effective_recipe_not_verified: '核验现货研究实际运行的配方版本',
  spot_asset_routes_not_verified: '核验该资产的研究数据与工具路线',
  spot_current_data_not_verified: '核验完整分钟路径与时点资格',
}
export function ResearchMarketPanel() {
  const query = useQuery({ queryKey: ['research-market-catalog'], queryFn: assets.researchMarkets, retry: false, staleTime: 60_000 })
  return <section className="product-card" aria-label="研究范围与开通条件"><h2>研究范围与开通条件</h2>
    <p className="product-muted">本目录展示常规研究开放政策，不代表当前服务、行情时效或实际运行配方已通过验收。</p>
    {query.isPending ? <p>读取研究范围…</p> : query.isError ? <AssetError retry={() => void query.refetch()} /> : query.data.items.map(scope => <article className="market-scope-card" key={scope.scope_id}>
      <h3>{scope.label}</h3><strong>{scope.research_enabled ? '常规研究已开通' : '常规研究尚未开通'}</strong>
      <p>{scope.instrument === 'XAU-USD' ? '计划研究美元计价的现货黄金；不与期货、ETF 或定盘基准混用。' : scope.instrument_kind === 'spot' ? `研究${scope.instrument.startsWith('ETH-') ? '以太坊' : '比特币'}现货与宏观变化，采用该资产独立的分钟路径。` : '研究比特币永续市场与宏观变化；日线现货参考单独展示。'}</p>
      {!scope.research_enabled && scope.instrument_kind === 'spot' && ['BTC-USDT', 'ETH-USDT'].includes(scope.instrument) && <p>现货研究处于受控试运行阶段，完整报告仍待验收。请在“今日研究”核对该资产的执行记录与报告资格。</p>}
      {scope.blocking_reasons.length > 0 && <><p>正式开通需满足以下条件：</p><ul>{scope.blocking_reasons.map(reason => <li key={reason}>{reasons[reason] ?? '该范围还有待核验条件'}</li>)}</ul></>}
      <details><summary>查看范围定义</summary><p>{scope.instrument} · {scope.price_unit}</p><p>领域规则：{scope.domain_pack_ref} · 目录版本：{query.data.registry_version}</p></details>
    </article>)}
  </section>
}
