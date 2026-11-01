"""Read current-template package chain in the preparation's one SQL snapshot."""
from dataclasses import dataclass
from datetime import datetime
from packages.kernel.decision_hub_kernel.application.research_skill_behavior_materials import LearningBehaviorMaterials
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import LearningRepository, reference
from packages.orchestration.langgraph.research_recipe_learning import ResearchRecipeLearningHandler
from packages.kernel.decision_hub_kernel.application.research_skill_package import decoded, encoded, sha

@dataclass(frozen=True)
class TrialMaterialRead:
    binding: object
    learning_protocol: object
    stage: object
    parent: object
    candidate: object
    snapshot: dict
    originals: tuple
    context: dict | None = None

    def verify_bytes(self):
        for archive, ref, digest in self.originals:
            if sha(archive.read(ref)) != digest:
                raise ValueError('trial_material_original_changed')

class TrialMaterialReader(LearningBehaviorMaterials):

    def read_in_session(self, session, job_id: str, package_receipt_id: str):
        repo = LearningRepository(session)
        binding = repo.binding(job_id)
        if binding is None:
            raise ValueError('trial_learning_job_missing')
        protocol = repo.protocol(binding.protocol.ref)
        stages = {name: repo.stage(job_id, name) for name in ('context', 'dispatch', 'reflection', 'package')}
        if any((item is None for item in stages.values())):
            raise ValueError('trial_package_chain_missing')
        stage = stages['package']
        if stage.id != package_receipt_id:
            raise ValueError('trial_package_stage_identity_mismatch')
        bodies = {name: self._payload(item) for name, item in stages.items()}
        body, context, reflection = (bodies['package'], bodies['context'], bodies['reflection'])
        snapshot = self.intake._snapshot(binding)
        if context['source'] != snapshot or reflection['dispatch_receipt'] != reference(stages['dispatch']).model_dump(mode='json'):
            raise ValueError('trial_context_or_reflection_changed')
        if protocol.parent_basis != 'registered_template':
            raise ValueError('trial_consumed_parent_reader_unavailable')
        parent = self.packages.read_in_session(session, protocol.parent_manifest.ref)
        candidate = self.packages.read_in_session(session, body['version_id'])
        parent_files = {item.path: {'body': item.body.decode('utf8'), 'hash': item.sha256} for item in parent.resources}
        if context['protocol'] != protocol.model_dump(mode='json') or context['binding'] != binding.model_dump(mode='json') or context['parent_registration']['manifest'] != parent.manifest.model_dump(mode='json') or (context['parent_files'] != parent_files) or (datetime.fromisoformat(parent.version['first_known_at']) > protocol.registered_at):
            raise ValueError('trial_parent_context_mismatch')
        delta = ResearchRecipeLearningHandler._validate_reflection(reflection['result']['payload'], context, protocol)
        if delta.outcome != 'candidate':
            raise ValueError('trial_nonseed_candidate_required')
        if parent.manifest.payload_hash != protocol.parent_manifest.hash or candidate.manifest.parent_version_id != parent.manifest.version_id or candidate.manifest.version_id == parent.manifest.version_id or ((parent.manifest.scope_id, candidate.manifest.scope_id) != (protocol.scope_id, protocol.scope_id)) or (candidate.version['horizon'] != protocol.horizon) or (body['status'] != 'registered_unadmitted') or (body['manifest_hash'] != candidate.manifest.payload_hash) or (body['financial_qualified'] is not False) or (body['trial_admitted'] is not False) or (body['parent_manifest'] != protocol.parent_manifest.model_dump(mode='json')) or (body['origin_refs'] != [binding.source.model_dump(mode='json'), reference(stages['reflection']).model_dump(mode='json')]):
            raise ValueError('trial_package_binding_mismatch')
        expected = {item.path: item.body for item in parent.resources}
        for change in delta.changes:
            if sha(expected[change.path]) != change.before_hash:
                raise ValueError('trial_reflection_before_changed')
            expected[change.path] = change.after_body.encode()
        workflow = decoded(expected['workflow.json'])
        workflow['version_id'] = candidate.manifest.version_id
        expected['workflow.json'] = encoded(workflow)
        actual = {item.path: item.body for item in candidate.resources}
        if expected != actual or body['resource_hashes'] != {key: sha(raw) for key, raw in actual.items()}:
            raise ValueError('trial_candidate_resources_changed')
        for field in ('evaluation_protocol_hash', 'schema_bindings', 'input_contract', 'output_contract', 'compiler_hash'):
            if candidate.version[field] != parent.version[field]:
                raise ValueError('trial_compiler_compatibility_required')
        if datetime.fromisoformat(candidate.version['first_known_at']) > stage.recorded_at:
            raise ValueError('trial_candidate_not_available_at_stage')
        originals = [(self.archive, binding.source_snapshot.ref, binding.source_snapshot.hash)]
        originals.extend(((self.archive, item.payload.ref, item.payload.hash) for item in stages.values()))
        for package in (parent, candidate):
            originals.extend(((self.packages.archive, item['body_ref'], item['body_hash']) for item in package.version['files']))
            originals.extend(((self.packages.archive, item['controlled_routes_ref'], item['controlled_routes_hash']) for item in package.version['capability_surfaces']))
        value = TrialMaterialRead(binding, protocol, stage, parent, candidate, snapshot, tuple(originals), context)
        value.verify_bytes()
        return value
