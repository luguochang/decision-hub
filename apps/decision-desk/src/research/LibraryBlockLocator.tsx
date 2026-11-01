import { useState } from 'react'
import type { DocumentBlock } from '../api/types'

export function locateOriginalBlocks(blocks: DocumentBlock[], versionId: string, literal: string) {
  // Indices refer to the original, unchanged pagination array.
  return blocks.flatMap((block, index) => block.version_id !== versionId ? [] :
    literal ? block.content.includes(literal) ? [{ block, index }] : [] :
    block.block_type === 'heading' ? [{ block, index }] : [])
}
export function LibraryBlockLocator({ blocks, versionId, onLocate }: { blocks: DocumentBlock[]; versionId: string; onLocate: (index: number) => void }) {
  const [literal, setLiteral] = useState('')
  const matches = locateOriginalBlocks(blocks, versionId, literal)
  return <section aria-label="原文查找与目录" className="library-section">
    <h3>定位原文</h3><p>目录只使用解析器标为标题的原文块。文字查找按原文逐字匹配，未生成摘要。</p>
    <label>查找原文文字<input type="search" aria-label="查找原文文字" maxLength={256} value={literal} onChange={event => setLiteral(event.target.value)} placeholder="输入原文中的文字" /></label>
    <p role="status">{literal ? `找到 ${matches.length} 个原文块` : matches.length ? `${matches.length} 个原文标题` : '本版没有标记的标题，可输入文字查找。'}{matches.length > 25 ? '，先显示前 25 项；缩小查找范围可定位其余内容。' : ''}</p>
    <ul>{matches.slice(0, 25).map(({ block, index }) => <li key={block.block_id}><button className="text-button" type="button" title={block.locator} onClick={() => onLocate(index)}>{block.content.slice(0, 160)}{block.content.length > 160 ? '…' : ''}</button><small> · 第 {block.page_no} 页 · 第 {index + 1} 段</small></li>)}</ul>
  </section>
}
