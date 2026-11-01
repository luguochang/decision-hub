import {createHash} from 'node:crypto'
import {readFileSync} from 'node:fs'
import {expect,it} from 'vitest'
import {registerProviderInputReader,verifySynthesisProviderInput,providerCanonicalJson} from '../src/host/provider-input.js'

it('authenticates phase raw arguments separately from canonical candidate identity',async()=>{
 const value=JSON.parse(readFileSync(new URL('../../../../artifacts/d2-native-preparation/draft-fixture.json',import.meta.url),'utf8'))
 const binding={runId:'run',requestHash:'a'.repeat(64),sessionId:'root',requestId:'rpc',generation:1},root={}
 const context:any={binding,request:{output_schema_ref:'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2',deadline_at:'2030-01-01T00:00:00Z'}}
 let proof:any
 const dispose=registerProviderInputReader(root,{linkBySession:async()=>({run_id:'run',generation:1,dsh_session_id:'root',request_hash:binding.requestHash,harness_required:false} as any),providerToolEmission:async()=>proof})
 try{
  for(const args of [{action:'prepare',request_id:value.options.request_id,draft:value.draft},{action:'finalize',request_id:value.options.request_id,prepared_call_id:'prepare-call'}]){
   proof={run_id:'run',generation:1,root_session_id:'root',execution_session_id:'root',call_id:'call',tool_name:'decision_hub_synthesis_submit',arguments_hash:createHash('sha256').update(providerCanonicalJson(args)).digest('hex'),candidate_hash:null}
   expect(await verifySynthesisProviderInput(root,context,'root','call',args,new AbortController().signal)).toBe(proof)
   proof.candidate_hash='b'.repeat(64)
   await expect(verifySynthesisProviderInput(root,context,'root','call',args,new AbortController().signal)).rejects.toThrow('identity_mismatch')
  }
 }finally{dispose()}
})
