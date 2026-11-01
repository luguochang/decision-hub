"""Build real immutable non-seed packages; registration never grants trial admission."""
import hashlib
import json
from pathlib import Path
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import LearningRepository, encoded
from packages.orchestration.langgraph.research_recipe_learning import ResearchRecipeLearningHandler
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def validate_supported_workflow_layout(parent, candidate):
    """The normal executor supports this fixed layout, not arbitrary compiled DAGs."""
    expected = {
        'spot_evidence': 'evidence.acquire',
        'features': 'component.compute',
        'hypothesis': 'hypothesis.check',
    }
    parent_nodes = {node['id']: node for node in parent['nodes']}
    candidate_nodes = {node['id']: node for node in candidate['nodes']}
    if (
        len(parent['nodes']) != 3
        or {key: node['op'] for key, node in parent_nodes.items()} != expected
        or len(candidate['nodes']) != 3
        or set(candidate_nodes) != set(parent_nodes)
    ):
        raise ValueError('learning_package_executor_layout_unsupported')
    if {key: value for key, value in candidate.items() if key != 'nodes'} != {
        key: value for key, value in parent.items() if key != 'nodes'
    }:
        raise ValueError('learning_package_executor_layout_unsupported')
    for name, original in parent_nodes.items():
        changed = candidate_nodes[name]
        if {key: value for key, value in changed.items() if key != 'inputs'} != {
            key: value for key, value in original.items() if key != 'inputs'
        } or set(changed['inputs']) != set(original['inputs']):
            raise ValueError('learning_package_executor_layout_unsupported')
        for key, binding in original['inputs'].items():
            proposed = changed['inputs'][key]
            if name == 'spot_evidence' and binding.get('from') == 'literal':
                if set(proposed) != {'from', 'value'} or proposed['from'] != 'literal':
                    raise ValueError('learning_package_executor_layout_unsupported')
            elif proposed != binding:
                raise ValueError('learning_package_executor_layout_unsupported')


class LearningPackageBuilder:

    def __init__(self, packages, contexts, author_root: Path, *, learning_archive):
        self.packages, self.contexts = (packages, contexts)
        self.author_root = author_root.resolve()
        self.learning_archive = learning_archive

    def _payload(self, receipt):
        raw = self.learning_archive.read(receipt.payload.ref)
        if hashlib.sha256(raw).hexdigest() != receipt.payload.hash:
            raise ValueError('learning_package_stage_raw_changed')
        value = json.loads(raw)
        if encoded(value).encode() != raw:
            raise ValueError('learning_package_stage_encoding_changed')
        return value

    def build(self, *, binding, protocol, reflection, reflection_receipt, context):
        if reflection.outcome != 'candidate' or reflection.parent_manifest != protocol.parent_manifest:
            raise ValueError('learning_package_candidate_required')
        if reflection_receipt.job_id != binding.job_id or reflection_receipt.phase != 'reflection':
            raise ValueError('learning_package_reflection_binding_mismatch')
        with self.packages.registry.database.session() as session:
            repository = LearningRepository(session)
            saved_binding = repository.binding(binding.job_id)
            saved_protocol = repository.protocol(protocol.id)
            saved_reflection = repository.stage(binding.job_id, 'reflection')
            saved_context = repository.stage(binding.job_id, 'context')
            saved_dispatch = repository.stage(binding.job_id, 'dispatch')
            parent = self.packages.read_in_session(session, protocol.parent_manifest.ref)
        if saved_binding != binding or saved_protocol != protocol or saved_reflection != reflection_receipt or (saved_context is None) or (saved_dispatch is None):
            raise ValueError('learning_package_original_binding_mismatch')
        response = self._payload(saved_reflection)
        if self._payload(saved_context) != context or response['result']['payload'] != reflection.model_dump(mode='json') or response['dispatch_receipt'] != {'ref': saved_dispatch.id, 'hash': saved_dispatch.payload_hash}:
            raise ValueError('learning_package_original_payload_mismatch')
        ResearchRecipeLearningHandler._validate_reflection(response['result']['payload'], context, protocol)
        if parent.manifest.payload_hash != protocol.parent_manifest.hash:
            raise ValueError('learning_package_parent_changed')
        version_id = 'learned_skill_' + canonical_hash([binding.payload_hash, protocol.parent_manifest.hash, reflection_receipt.payload_hash, reflection.model_dump(mode='json')])[:40]
        files = {item.path: item.body for item in parent.resources}
        changes = {item.path: item for item in reflection.changes}
        if not changes or len(changes) != len(reflection.changes):
            raise ValueError('learning_package_real_change_required')
        for name, change in changes.items():
            if name not in protocol.allowed_resource_paths or name not in files:
                raise ValueError('learning_package_resource_not_allowed')
            if hashlib.sha256(files[name]).hexdigest() != change.before_hash:
                raise ValueError('learning_package_before_hash_mismatch')
            after = change.after_body.encode('utf8')
            if after == files[name] or not 0 < len(after) <= 1000000:
                raise ValueError('learning_package_change_invalid')
            files[name] = after
        workflow = json.loads(files['workflow.json'])
        if workflow['version_id'] != parent.manifest.version_id:
            raise ValueError('learning_package_model_version_forbidden')
        if any((node['op'] not in protocol.allowed_operations for node in workflow['nodes'])):
            raise ValueError('learning_package_operation_not_registered')
        if not set(workflow['required_capabilities']).issubset(protocol.allowed_capabilities):
            raise ValueError('learning_package_capability_not_registered')
        original_workflow = json.loads(next(item.body for item in parent.resources if item.path == 'workflow.json'))
        validate_supported_workflow_layout(original_workflow, workflow)
        workflow['version_id'] = version_id
        files['workflow.json'] = encoded(workflow).encode()
        compiler_context = self.contexts.get(protocol.scope_id)
        if compiler_context is None:
            raise ValueError('learning_package_compiler_unavailable')
        author = self.author_root / version_id
        if not author.is_relative_to(self.author_root) or author.is_symlink() or author.is_junction():
            raise ValueError('learning_package_author_path_invalid')
        author.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            path = author / name
            if not path.resolve().is_relative_to(author) or path.is_symlink() or path.is_junction():
                raise ValueError('learning_package_resource_path_invalid')
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('xb') as stream:
                    stream.write(data)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError('learning_package_author_bytes_changed') from None
        prepared = self.packages.prepare(author, skill_id=parent.version['skill_id'], version_id=version_id, context=compiler_context, parent_version_id=parent.manifest.version_id)
        version = prepared.version
        for field in ('evaluation_protocol_hash', 'schema_bindings', 'input_contract', 'output_contract', 'compiler_hash'):
            if version[field] != parent.version[field]:
                raise ValueError('learning_package_immutable_contract_changed')
        manifest = self.packages.register(prepared)
        return {'schema_version': 'research-learned-package.v1', 'status': 'registered_unadmitted', 'version_id': version_id, 'manifest_hash': manifest.payload_hash, 'first_known_at': version['first_known_at'], 'parent_manifest': protocol.parent_manifest.model_dump(mode='json'), 'source_method': protocol.source_method.model_dump(mode='json'), 'parent_basis': protocol.parent_basis, 'origin_refs': [binding.source.model_dump(mode='json'), {'ref': reflection_receipt.id, 'hash': reflection_receipt.payload_hash}], 'lineage_stored_in': 'immutable_learning_package_stage', 'resource_hashes': {item.path: item.sha256 for item in prepared.resources}, 'financial_qualified': False, 'trial_admitted': False, 'requires': ['named_behavior_controls', 'registered_trial', 'actual_run_consumption', 'paired_outcomes']}
