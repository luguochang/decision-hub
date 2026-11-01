"""Independent persisted behavior read: replay assertions, never invoke a model."""
from dataclasses import dataclass
from packages.kernel.decision_hub_kernel.ports.research_learning import BehaviorReceipt, BehaviorOriginalRef as Ref
from packages.kernel.decision_hub_kernel.application.research_skill_behavior import ref
from jsonschema import Draft202012Validator
from packages.kernel.decision_hub_kernel.application.research_skill_package import decoded, encoded, sha
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.runtime_adapters.execution_recipe import recipe_hash

@dataclass(frozen=True)
class BehaviorRead:
    protocol: object
    receipt: BehaviorReceipt
    archive: object
    originals: tuple

    def verify_bytes(self):
        for locator, digest in self.originals:
            if sha(self.archive.read(locator)) != digest:
                raise ValueError('trial_behavior_original_changed')

class BehaviorOriginalReader:

    def __init__(self, store, archive):
        self.store, self.archive = (store, archive)

    def read_in_session(self, session, protocol_id, receipt_id, materials, *, now):
        protocol = self.store.read_in_session(session, 'protocol', protocol_id)
        old = self.store.read_in_session(session, 'receipt', protocol_id)
        if protocol is None or old is None or old.id != receipt_id:
            raise ValueError('trial_behavior_receipt_missing')
        if protocol.job_id != materials.binding.job_id or protocol.package_stage != Ref(ref=materials.stage.id, hash=materials.stage.payload_hash) or protocol.parent != Ref(ref=materials.parent.manifest.version_id, hash=materials.parent.manifest.payload_hash) or (protocol.candidate != Ref(ref=materials.candidate.manifest.version_id, hash=materials.candidate.manifest.payload_hash)) or (protocol.registered_at < materials.stage.recorded_at):
            raise ValueError('trial_behavior_package_mismatch')
        from .research_learning_trial_protocols import require_frozen_cases
        if materials.context is None:
            raise ValueError('trial_behavior_frozen_suite_required')
        require_frozen_cases(protocol, materials.context, self.archive)
        originals = []

        def read(reference):
            raw = self.archive.read(reference.ref)
            if sha(raw) != reference.hash:
                raise ValueError('trial_behavior_original_changed')
            body = decoded(raw)
            if encoded(body) != raw:
                raise ValueError('trial_behavior_encoding_changed')
            originals.append((reference.ref, reference.hash))
            return body
        attempt_refs, output_refs, modes = ([], [], set())
        passed = {'parent': True, 'candidate': True}
        for case in protocol.cases:
            case_input, expected = (read(case.inputs), read(case.expected))
            Draft202012Validator.check_schema(expected)
            validator = Draft202012Validator(expected)
            for slot in ('parent', 'candidate'):
                package = getattr(materials, slot)
                method = getattr(protocol, slot)
                resources = {item.path: {'body': item.body.decode('utf8'), 'hash': item.sha256} for item in package.resources}
                expected_input = {'protocol': protocol.id, 'case': case.id, 'method': method.model_dump(mode='json'), 'resources': resources, 'case_input': case_input}
                key = canonical_hash([protocol.payload_hash, case.id, slot])
                attempt = self.store.read_in_session(session, 'attempt', key)
                output = self.store.read_in_session(session, 'output', key)
                if attempt is None or output is None:
                    raise ValueError('trial_behavior_completion_unconfirmed')
                if attempt.protocol != ref(protocol) or attempt.method != method or attempt.method_slot != slot or (attempt.case_id != case.id) or (attempt.expected_original != case.expected) or (attempt.recipe_hash != protocol.recipe_hash) or (not protocol.registered_at <= attempt.created_at <= output.completed_at <= old.completed_at <= now) or (output.attempt != ref(attempt, 'key')) or (output.mode != attempt.mode) or (read(attempt.input_original) != expected_input):
                    raise ValueError('trial_behavior_attempt_binding_mismatch')
                actual, runtime = (read(output.actual_output), read(output.runtime_original))
                if runtime.get('payload') != actual or runtime.get('mode') != attempt.mode:
                    raise ValueError('trial_behavior_runtime_original_mismatch')
                if attempt.mode == 'live_model':
                    recipe, request, result = (runtime.get('recipe', {}), runtime.get('request', {}), runtime.get('result', {}))
                    if recipe.get('live_attestable') is not True or recipe_hash(recipe) != protocol.recipe_hash or runtime.get('recipe_hash') != protocol.recipe_hash or (request.get('text') != encoded(expected_input).decode()) or (request.get('role') != 'research_method_behavior') or (result.get('payload') != actual) or (result.get('runtime_id') != output.runtime_id) or output.runtime_id.lower().startswith(('fake', 'replay', 'controlled')):
                        raise ValueError('trial_behavior_live_original_required')
                passed[slot] = passed[slot] and validator.is_valid(actual)
                modes.add(attempt.mode)
                attempt_refs.append(ref(attempt, 'key'))
                output_refs.append(Ref(ref=key, hash=canonical_hash(output.model_dump(mode='json'))))
        if len(modes) != 1:
            raise ValueError('trial_behavior_mixed_execution_modes')
        mode = next(iter(modes))
        status = 'control_only' if mode == 'controlled_model' else 'behavior_passed' if passed['candidate'] else 'behavior_failed'
        body = dict(schema_version='skill-behavior-receipt.v1', id='behavior_' + protocol.payload_hash, protocol=ref(protocol), package_stage=protocol.package_stage, attempts=attempt_refs, outputs=output_refs, candidate_passed=passed['candidate'], parent_passed=passed['parent'], execution_kind=mode, status=status, completed_at=old.completed_at, payload_hash='0' * 64)
        wire = BehaviorReceipt.model_validate(body).model_dump(mode='json', exclude={'payload_hash'})
        calculated = BehaviorReceipt.model_validate(wire | {'payload_hash': canonical_hash(wire)})
        if old != calculated:
            raise ValueError('trial_behavior_declared_result_mismatch')
        return BehaviorRead(protocol, calculated, self.archive, tuple(originals))
