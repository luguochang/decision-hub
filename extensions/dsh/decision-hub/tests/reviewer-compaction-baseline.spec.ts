import { expect, it } from 'vitest'
import { foldSurface } from '@deepseek-ai/dsh-session/surface'
import { hasExactReviewerPrompt } from '../src/reviewer-system-snapshot.js'
const baseline=(seq:number):any=>({seq,type:'user/message',surfaceOp:'append',data:{role:'user',id:'baseline-'+seq,
 source:{kind:'agent-instructions',form:'instructions',baseline:true,baselineIdentity:'synthetic-config',changes:[{action:'set',scope:'.',path:'AGENTS.md',digest:'a'.repeat(40)}]},content:[{type:'text',text:'Synthetic instructions.'}]}})
const call=(seq:number):any=>({seq,type:'tool/call',data:{callId:'review',name:'subagent_reviewer'}})
function prefix():any[]{return [{seq:0,type:'turn/start',data:{}},baseline(1),{seq:2,type:'compaction/start',data:{}},
 {seq:3,type:'user/message',surfaceOp:{op:'replace',start:1,end:1},sourceEventSeqs:[1,2],data:{role:'user',id:'summary',source:{kind:'compact'},content:[{type:'text',text:'Summary.'}]}},
 baseline(4),call(5)]}
const child=():any[]=>[{seq:0,type:'user/message',data:{content:[{type:'text',text:'product'}]}},baseline(1),{seq:2,type:'assistant/message',data:{message:{content:[]}}}]
const check=(parent=prefix(),cutoff=5,children=child())=>hasExactReviewerPrompt(children,'product',parent,cutoff)
it('accepts a genuine canonical SDK replacement and identical baseline re-emission without changing originals',()=>{
 const parent=prefix(),children=child(),before=JSON.stringify({parent,children})
 expect(foldSurface(parent.slice(0,5) as any).nodes).toEqual([3,4])
 expect(check(parent,5,children)).toBe(true)
 expect(JSON.stringify({parent,children})).toBe(before)
})
it('accepts repeated legal shadowing, with only one identical baseline active',()=>{
 const parent=prefix().slice(0,5)
 parent.push({seq:5,type:'user/message',surfaceOp:{op:'replace',start:3,end:4},sourceEventSeqs:[3,4],data:{role:'user',id:'summary2',content:[{type:'text',text:'Summary two.'}] }},baseline(6),call(7))
 expect(check(parent,7)).toBe(true)
})
it.each(['bare','provenance','range','replacement','changed','delta','active-duplicate','sparse','packed','after-cutoff','wrong-cutoff','partial-multi'])('rejects %s parent authority',mode=>{
 const parent=prefix();let cutoff=5
 if(mode==='bare') {parent[3]={seq:3,type:'step/start',data:{}}}
 if(mode==='provenance') parent[3].sourceEventSeqs=[2]
 if(mode==='range') parent[3].surfaceOp.end=2
 if(mode==='replacement') parent[3].surfaceOp.op='unknown'
 if(mode==='changed') parent[1].data.content[0].text+=' old'
 if(mode==='delta') parent[1].data.source.baseline=false
 if(mode==='active-duplicate') {parent.splice(5,0,baseline(5));parent[6].seq=6;cutoff=6}
 if(mode==='sparse') parent.splice(2,1)
 if(mode==='packed') parent.splice(2,2,{seq:2,type:'assistant/chunk-group',data:{}})
 if(mode==='after-cutoff') {parent[3]=baseline(3);parent[4]=call(4);parent[5]={seq:5,type:'user/message',surfaceOp:{op:'replace',start:1,end:3},sourceEventSeqs:[1,3],data:{role:'user',id:'late-summary',content:[{type:'text',text:'Late summary.'}]}};parent.push(baseline(6));cutoff=4}
 if(mode==='wrong-cutoff') cutoff=6
 if(mode==='partial-multi') parent.shift()
 expect(check(parent,cutoff)).toBe(false)
})
it('rejects duplicate child baseline despite legally compacted parent authority',()=>{
 const children=child();children.splice(2,0,baseline(2));expect(check(prefix(),5,children)).toBe(false)
})
it('does not let later parent updates replace original call authority',()=>{
 const parent=prefix();const later=baseline(6);later.data.content[0].text+=' changed';parent.push(later)
 expect(check(parent)).toBe(true)
})
