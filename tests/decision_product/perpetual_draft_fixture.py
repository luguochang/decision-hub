"""Authored fixture conversion only; never a source/authentication implementation."""
from copy import deepcopy
import json

def draft_from_packet(packet, originals):
 s=deepcopy(packet['synthesis']); assumptions=[]
 event_definition=deepcopy(s['event_probability']['event_definition_ref'])
 available={json.dumps(ref,sort_keys=True) for row in originals['originals'] for ref in (row['source_ref'],row['original_ref']) if ref}
 def local(r,role):
  if r is None or json.dumps(r,sort_keys=True) in available:return r
  ident=role+':'+r['ref']
  if not any(a['assumption_id']==ident for a in assumptions):assumptions.append(dict(schema_version='decision-model-assumption.v1',assumption_id=ident,role=role,statement='Synthetic declared '+role+' assumption; no source or calibration claim.',evidence_refs=[]))
  return dict(local_id=ident)
 def estimate(p,direction=False,role='occurrence_target'):
  if direction:
   p.pop('target_ref');p.pop('conditioning_ref')
  else:
   p['target_ref']=local(p['target_ref'],role);p['conditioning_ref']=local(p['conditioning_ref'],'event_definition' if p['conditioning_ref']==event_definition else 'conditioning')
  p['source_ref']=local(p['source_ref'],'probability_source')
  return p
 candidates=[]
 for c in s['candidate_set']['candidates']:
  nested=next((x['candidate_id'] for x,r in zip(s['candidate_set']['candidates'],s['candidate_set']['candidate_refs']) if r==c['nested_action_ref']),None)
  for key in ['schema_version','context','valid_until','execution','nested_action_ref']:c.pop(key)
  c['nested_candidate_id']=nested
  for key,role in [('condition_refs','entry_condition'),('target_refs','action_target'),('basis_refs','basis')]:c[key]=[local(r,role) for r in c[key]]
  for key,role in [('size_policy_ref','size_policy'),('exit_ref','exit_policy')]:c[key]=local(c[key],role)
  candidates.append(c)
 event=s['event_probability'];event.pop('schema_version')
 for key,role in [('event_definition_ref','event_definition'),('occurrence_target_ref','occurrence_target'),('outcome_target_ref','outcome_target'),('joint_partition_ref','joint_partition'),('observation_rule_ref','observation_rule')]:event[key]=local(event[key],role)
 event['occurrence_distribution']=estimate(event['occurrence_distribution']);event['outcome_given_occurrence']=estimate(event['outcome_given_occurrence'],role='outcome_target')
 paths=s['path_distribution'];paths.pop('market_target_ref');paths.pop('conditioning_ref');paths['fill_weight_assumption_ref']=local(paths['fill_weight_assumption_ref'],'fill_weight_assumption')
 analysis=s['analysis'];analysis.pop('schema_version');analysis.pop('candidate_target_ref')
 return dict(schema_version='perpetual-synthesis-draft.v1',as_of=s['as_of'],known_at_max=s['known_at_max'],candidate_set_id=s['candidate_set']['candidate_set_id'],valid_until=s['candidate_set']['valid_until'],assumptions=assumptions,candidates=candidates,event_probability=event,path_distribution=paths,direction_distribution=estimate(s['market_distribution']['direction_distribution'],True),market_event_conditioned=s['market_distribution']['event_scenario_ref'] is not None,economic_input_refs=[r for r in s['economic_input_refs'] if r!=s['market_distribution']['path_distribution_ref']],reasoning_refs=s['reasoning_refs'],analysis=analysis,gaps=s['gaps'])
