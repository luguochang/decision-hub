"""Freeze complete parent resources and authenticated source records for reflection."""
import hashlib
from datetime import datetime
from packages.kernel.decision_hub_kernel.ports.research_learning import OriginalRef, aware
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import encoded
from .research_learning_suite import freeze_behavior_suite, case_reference

class LearningContextBuilder:

    def __init__(self, packages, contexts, *, clock):
        self.packages, self.contexts, self.clock = (packages, contexts, clock)

    def build(self, binding, protocol, snapshot):
        if binding.protocol.ref != protocol.id or binding.protocol.hash != protocol.payload_hash or snapshot['source'] != binding.source.model_dump(mode='json') or (binding.parent_manifest != protocol.parent_manifest):
            raise ValueError('learning_context_binding_mismatch')
        if protocol.parent_basis != 'registered_template':
            raise ValueError('learning_consumed_parent_original_reader_required')
        with self.packages.registry.database.session() as session:
            parent = self.packages.read_in_session(session, protocol.parent_manifest.ref)
        manifest, version = (parent.manifest, parent.version)
        known = aware(datetime.fromisoformat(version['first_known_at']))
        if manifest.payload_hash != protocol.parent_manifest.hash or manifest.scope_id != protocol.scope_id or version['horizon'] != protocol.horizon or (known > protocol.registered_at) or (protocol.registered_at > binding.frozen_at) or (binding.frozen_at > aware(self.clock())):
            raise ValueError('learning_parent_registration_mismatch')
        installed = self.contexts.get(protocol.scope_id)
        if installed is None or installed.compiler_hash != self.packages.installed.code_hash:
            raise ValueError('learning_parent_compiler_context_unavailable')
        operations = {item.op for item in installed.operations}
        capabilities = {item.capability_id for item in installed.capabilities}
        if not set(protocol.allowed_operations).issubset(operations) or not set(protocol.allowed_capabilities).issubset(capabilities) or (not set(protocol.allowed_capabilities).issubset(installed.allowed_capabilities)):
            raise ValueError('learning_protocol_exceeds_installed_adapter')
        resources = {item.path: {'body': item.body.decode('utf8'), 'hash': item.sha256} for item in parent.resources}
        if any((hashlib.sha256(item['body'].encode()).hexdigest() != item['hash'] for item in resources.values())):
            raise ValueError('learning_parent_resource_hash_mismatch')
        if not set(protocol.allowed_resource_paths).issubset(resources):
            raise ValueError('learning_parent_modifiable_resource_missing')
        suite = freeze_behavior_suite(scope_id=protocol.scope_id, frozen_at=aware(self.clock()))
        refs = [binding.source, protocol.parent_manifest, OriginalRef(ref=protocol.id, hash=protocol.payload_hash)]
        refs.extend((OriginalRef(ref=item['body_ref'], hash=item['body_hash']) for item in version['files']))
        refs.extend(case_reference(case) for case in suite.cases)
        originals = snapshot['originals'].get('research_originals')
        declared = [] if originals is None else originals['sidecar']['synthesis']['skill_consumption_refs']
        context = {'schema_version': 'research-learning-context.v1', 'protocol': protocol.model_dump(mode='json'), 'binding': binding.model_dump(mode='json'), 'source': snapshot, 'parent_manifest': protocol.parent_manifest.model_dump(mode='json'), 'parent_registration': {'manifest': manifest.model_dump(mode='json'), 'first_known_at': known.isoformat()}, 'parent_files': resources, 'parent_relationship': {'basis': 'registered_template', 'consumed': False, 'consumption_claimed_by_this_context': False, 'declared_source_consumption_refs': declared, 'financial_attribution_allowed': False, 'reason': 'Explicit registered improvement template; no authenticated source consumption is claimed.'}, 'implemented_operations': sorted(set(protocol.allowed_operations)), 'allowed_capabilities': sorted(set(protocol.allowed_capabilities)), 'allowed_refs': [item.model_dump(mode='json') for item in {(item.ref, item.hash): item for item in refs}.values()], 'source_semantics': {'label_available_at': binding.source_available_at.isoformat(), 'retrospective_labels_must_not_be_backfilled': True, 'evidence_ref_is_fulltext': False, 'untrusted_resource_text': True, 'financial_qualified': False}}
        context['behavior_suite'] = suite.model_dump(mode='json')
        encoded(context)
        return context
