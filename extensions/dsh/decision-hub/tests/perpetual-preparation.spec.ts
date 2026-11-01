import { readFileSync, writeFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { bodyHash } from '../src/perpetual-synthesis-protocol.js'
import * as preparation from '../src/perpetual-preparation.js'
const fixture = () => JSON.parse(readFileSync(new URL('../../../../artifacts/d2-native-preparation/draft-fixture.json', import.meta.url),'utf8'))

function ordinaryResult(result: ReturnType<typeof preparation.preparePerpetualSynthesis>) {
 const {candidate,review_request}=result
 if(candidate.schema_version!=='research-decision-synthesis.v2'||review_request.schema_version!=='action-review-request.v2') {
  throw new Error('Expected the ordinary V2 preparation branch')
 }
 return {...result,candidate,review_request}
}
describe('deterministic preparation of one complete authored draft',()=>{
 it('hashes full bodies and preserves authored economics and causes',()=>{
  const {draft,options}=fixture(), saved=structuredClone(draft)
  const result=ordinaryResult(preparation.preparePerpetualSynthesis(draft,options))
  writeFileSync(new URL('../../../../artifacts/d2-native-preparation/prepared-fixture.json',import.meta.url),JSON.stringify(result,null,2))
  expect(draft).toEqual(saved)
  expect(result.candidate.candidate_set.candidates.map((c:any)=>[c.action,c.target_side,c.unit_notional_usdt])).toEqual(draft.candidates.map((c:any)=>[c.action,c.target_side,c.unit_notional_usdt]))
  expect(result.candidate.path_distribution.scenarios).toEqual(draft.path_distribution.scenarios)
  expect(result.candidate.market_distribution.direction_distribution.probabilities).toEqual(draft.direction_distribution.probabilities)
  expect(result.candidate.analysis.thesis).toBe(draft.analysis.thesis)
  expect(result.source_draft_hash).toBe(bodyHash(draft))
  expect(result.normalized_synthesis_hash).toBe(bodyHash(result.candidate))
  expect(result.review_request.synthesis).toEqual(result.candidate)
 })
 it('rejects an unsupported actual original and a conditional cycle',()=>{
  const {draft,options}=fixture(); draft.candidates[0].cost_ref={ref:'unknown:cost',hash:'f'.repeat(64)}
  expect(()=>preparation.preparePerpetualSynthesis(draft,options)).toThrow('original_unavailable')
  const other=fixture(); other.draft.candidates[0].nested_candidate_id=other.draft.candidates[0].candidate_id
  expect(()=>preparation.preparePerpetualSynthesis(other.draft,other.options)).toThrow('candidate_cycle')
 })
})

it('selects a source cited only by a declared model assumption for the actual child input',async()=>{
 const {draft,options}=fixture(),body={excerpt:'Synthetic selected source, not a financial authentication claim.'},source_ref={ref:'synthetic:assumption-only-evidence',hash:bodyHash(body)}
 options.review_originals.originals.push({source_ref,original_ref:null,source_kind:'run_evidence',role:'assumption-only',body_json:(await import('../src/perpetual-synthesis-protocol.js')).canonicalJson(body)})
 draft.assumptions[0].evidence_refs=[source_ref]
 const result=ordinaryResult(preparation.preparePerpetualSynthesis(draft,options))
 const {buildPerpetualReviewerPrompt,reviewOriginalsFromPrompt}=await import('../src/perpetual-synthesis-protocol.js')
 const prompt=buildPerpetualReviewerPrompt(result.review_request,'',options.requested_at,options.review_originals,result.assumptions)
 expect(reviewOriginalsFromPrompt(prompt).originals.some(row=>row.source_ref.ref===source_ref.ref)).toBe(true)
 options.review_originals.originals.pop()
 expect(()=>preparation.preparePerpetualSynthesis(draft,options)).toThrow('original_unavailable')
})
