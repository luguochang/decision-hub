import { describe, expect, it } from 'vitest'
import type { ResearchDocument, ResearchDocumentVersion } from '../api/types'
import { libraryResearchRequest } from './libraryRequest'

const document = {
  document_id: 'doc_btc',
  market_scopes: ['crypto_macro'],
  instrument_scopes: ['BTC-USDT'],
} as ResearchDocument
const version = {
  document_id: 'doc_btc',
  version_id: 'docver_reviewed',
  status: 'ready',
} as ResearchDocumentVersion

describe('library research scope', () => {
  it('uses the selected reviewed version and its saved BTC scope', () => {
    expect(libraryResearchRequest(document, version).request).toMatchObject({
      research_family: 'crypto_macro',
      instrument_scope: ['BTC-USDT'],
      selected_version_ids: ['docver_reviewed'],
      allow_remote_library_content: false,
      horizon: '72h',
    })
  })

  it('blocks a selected version from another document or research domain', () => {
    expect(libraryResearchRequest(document, { ...version, document_id: 'doc_other' }).request).toBeNull()
    expect(libraryResearchRequest({ ...document, market_scopes: ['cn_equity'] }, version).request).toBeNull()
    expect(libraryResearchRequest({ ...document, instrument_scopes: ['ETH-USDT'] }, version).request).toBeNull()
  })

  it('records explicit consent only for the current request', () => {
    expect(libraryResearchRequest(document, version, true).request?.allow_remote_library_content).toBe(true)
    expect(libraryResearchRequest(document, version).request?.allow_remote_library_content).toBe(false)
  })

  it('blocks a version that has not passed review', () => {
    expect(libraryResearchRequest(document, { ...version, status: 'review' }).request).toBeNull()
  })
})
