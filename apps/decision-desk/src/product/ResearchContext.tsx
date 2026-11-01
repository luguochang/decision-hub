import type { ResearchRunView } from '../api/types'
import { researchDisplayTitle } from './researchLanguage'

type ResearchTitleInput = Pick<ResearchRunView, 'event_title'> &
  Partial<Pick<ResearchRunView, 'research_profile'>>

function topic(run: ResearchTitleInput, focus?: string | null) {
  if (run.research_profile === 'perpetual_decision_v2') return '永续合约事前研究'
  // Query views supply a non-null focus only for decision-desk-latest-advice.
  // A title string alone cannot distinguish that template from a user title.
  if (focus != null) return focus.replace(/\s+/g, ' ').trim() || 'BTC 与宏观快照研究'
  return researchDisplayTitle(run.event_title || '未命名研究任务')
}

export function researchTitle(run: ResearchTitleInput, focus?: string | null) {
  const text = topic(run, focus)
  const characters = Array.from(text)
  return characters.length > 36 ? `${characters.slice(0, 35).join('')}…` : text
}

/** Preserve the complete saved focus separately from the compact page heading. */
export function ResearchFocus({ run, focus }: { run: ResearchRunView; focus?: string | null }) {
  const text = focus ?? run.event_title
  if (!text || (focus == null && text === researchTitle(run, focus))) return null
  return <details className="research-focus"><summary>{focus != null ? '查看研究关注点' : '查看任务标题'}</summary><p>{text}</p></details>
}
