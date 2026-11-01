import { useQuery } from '@tanstack/react-query'
import { assets } from '../api/assets'
import { assetTime } from './assetFormat'
import { StudyEffectTracking } from './StudyEffectTracking'

export function StudyEffectTarget({ study, revision }: { study: string; revision: string }) {
  const original = useQuery({
    queryKey: ['study-effect-target', study, revision], retry: false,
    queryFn: async () => {
      const value = await assets.studyReportComparison(study, revision)
      if (value.study_id !== study || value.current.study_id !== study || value.current.revision_id !== revision
        || value.current.strategy_version !== 'research.v1' || !value.current.source_run_id || !value.current.artifact_id) {
        throw new Error('这版报告的原主题关联无法核对。')
      }
      return value.current
    },
  })
  if (original.isPending) return <p role="status">正在读取链接指定的报告版本…</p>
  if (original.isError) return <p role="alert">链接指定的报告版本暂时无法核对。<button className="product-link" onClick={() => void original.refetch()}>重新读取原版本</button></p>
  const saved = original.data
  return <div className="product-notice"><p>链接指定的报告 · {assetTime(saved.created_at)}。这版不在当前分页中，已按原版本单独读取。</p><StudyEffectTracking study={study} revision={saved.revision_id} run={saved.source_run_id} artifact={saved.artifact_id} /></div>
}
