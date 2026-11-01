"""Authenticate a nonseed package via its actual Learning and W5 originals.

This adapter accepts identities only. It reuses the existing authoritative W6
source intake and reflection validator, and does not accept a verified DTO.
"""
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import LearningRepository, reference
from packages.orchestration.langgraph.research_recipe_learning import ResearchRecipeLearningHandler
from packages.kernel.decision_hub_kernel.application.research_skill_package import decoded, encoded, sha

class LearningBehaviorMaterials:

    def __init__(self, packages, intake):
        if packages.registry.database is not intake.database:
            raise ValueError('behavior_material_database_mismatch')
        self.packages, self.intake = (packages, intake)
        self.database, self.archive = (intake.database, intake.archive)

    def _payload(self, receipt):
        raw = self.archive.read(receipt.payload.ref)
        if sha(raw) != receipt.payload.hash:
            raise ValueError('behavior_learning_original_changed')
        body = decoded(raw)
        if encoded(body) != raw:
            raise ValueError('behavior_learning_encoding_changed')
        return body

    def require_package_stage(self, job_id, stage_ref, parent_ref, candidate_ref):
        with self.database.session() as session:
            repository = LearningRepository(session)
            binding = repository.binding(job_id)
            if binding is None:
                raise ValueError('behavior_learning_job_missing')
            protocol = repository.protocol(binding.protocol.ref)
            stage = repository.stage(job_id, 'package')
            context = repository.stage(job_id, 'context')
            reflection = repository.stage(job_id, 'reflection')
            dispatch = repository.stage(job_id, 'dispatch')
            if any((value is None for value in (stage, context, reflection, dispatch))):
                raise ValueError('behavior_learning_package_chain_missing')
            if reference(stage).model_dump() != stage_ref.model_dump():
                raise ValueError('behavior_learning_package_stage_mismatch')
            parent = self.packages.read_in_session(session, parent_ref.ref)
            candidate = self.packages.read_in_session(session, candidate_ref.ref)
        snapshot = self.intake._snapshot(binding)
        identity, bodies, available = self.intake._read_source(binding.source_kind, binding.source.ref)
        if encoded(bodies) != encoded(snapshot['originals']) or available < binding.source_available_at or identity.source != binding.source:
            raise ValueError('behavior_learning_source_changed')
        context_body, reflection_body = (self._payload(context), self._payload(reflection))
        if context_body['source'] != snapshot or reflection_body['dispatch_receipt'] != reference(dispatch).model_dump(mode='json'):
            raise ValueError('behavior_learning_context_changed')
        delta = ResearchRecipeLearningHandler._validate_reflection(reflection_body['result']['payload'], context_body, protocol)
        if delta.outcome != 'candidate':
            raise ValueError('behavior_candidate_reflection_required')
        stage_body = self._payload(stage)
        if parent_ref.model_dump() != protocol.parent_manifest.model_dump() or parent_ref.hash != parent.manifest.payload_hash or candidate_ref.hash != candidate.manifest.payload_hash or (candidate.manifest.parent_version_id != parent.manifest.version_id) or (parent.manifest.scope_id != protocol.scope_id) or (candidate.manifest.scope_id != protocol.scope_id) or (candidate.version['horizon'] != protocol.horizon) or (stage_body['status'] != 'registered_unadmitted') or ((stage_body['version_id'], stage_body['manifest_hash']) != (candidate_ref.ref, candidate_ref.hash)) or (stage_body['financial_qualified'] is not False) or (stage_body['trial_admitted'] is not False) or (stage_body['parent_manifest'] != parent_ref.model_dump(mode='json')) or (stage_body['origin_refs'] != [binding.source.model_dump(mode='json'), reference(reflection).model_dump(mode='json')]):
            raise ValueError('behavior_nonseed_package_binding_mismatch')
        expected = {item.path: item.body for item in parent.resources}
        for change in delta.changes:
            if sha(expected[change.path]) != change.before_hash:
                raise ValueError('behavior_reflection_before_changed')
            expected[change.path] = change.after_body.encode()
        workflow = decoded(expected['workflow.json'])
        workflow['version_id'] = candidate_ref.ref
        expected['workflow.json'] = encoded(workflow)
        actual = {item.path: item.body for item in candidate.resources}
        if expected != actual or stage_body['resource_hashes'] != {name: sha(raw) for name, raw in actual.items()}:
            raise ValueError('behavior_candidate_resources_changed')
        for field in ('evaluation_protocol_hash', 'schema_bindings', 'input_contract', 'output_contract', 'compiler_hash'):
            if candidate.version[field] != parent.version[field]:
                raise ValueError('behavior_compiler_compatibility_required')
        return {'binding': binding, 'protocol': protocol, 'stage': stage, 'parent': parent, 'candidate': candidate}

    def read_method(self, method):
        with self.database.session() as session:
            value = self.packages.read_in_session(session, method.ref)
        if value.manifest.payload_hash != method.hash:
            raise ValueError('behavior_method_manifest_changed')
        return {item.path: {'body': item.body.decode('utf8'), 'hash': item.sha256} for item in value.resources}
