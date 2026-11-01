"""Read-only W6 adapter. A method under evaluation is not its parent skill package."""
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import ExecutionRepository
from packages.kernel.decision_hub_kernel.ports.research_learning import LearningSourceIdentity, OriginalRef

class ActionLearningSources:

    def __init__(self, service):
        self.service, self.database = (service, service.database)

    def _identity(self, source, *, trade):
        with self.database.session() as session:
            return self._identity_in_session(session, source, trade=trade)

    def _identity_in_session(self, session, source, *, trade):
        repository = ExecutionRepository(session)
        identity = repository.account(source['portfolio_id']).portfolio_identity
        protocol = repository.protocol(identity.protocol_ref)
        targets = [item for item in protocol.targets if item.horizon == identity.horizon]
        if protocol.payload_hash != identity.protocol_hash or len(targets) != 1 or targets[0].target_spec_hash != identity.target_spec_hash or (not any((item.ref == identity.method_ref and item.hash == identity.method_hash for item in protocol.methods))):
            raise ValueError('learning_portfolio_protocol_mismatch')
        if trade and (source['identity_hash'] != identity.identity_hash or source['scope_id'] != identity.scope_id or source['horizon'] != identity.horizon or (source['method_ref'] != identity.method_ref) or (source['method_hash'] != identity.method_hash) or (source['protocol_ref'] != protocol.id) or (source['protocol_hash'] != identity.identity_hash) or (source['registered_protocol_hash'] != protocol.payload_hash)):
            raise ValueError('learning_outcome_portfolio_mismatch')
        return LearningSourceIdentity(schema_version='research-learning-source-identity.v1', source=OriginalRef(ref=source['id'], hash=source['payload_hash']), portfolio=OriginalRef(ref=identity.id, hash=identity.identity_hash), scope_id=identity.scope_id, horizon=identity.horizon, target=OriginalRef(ref=targets[0].target_spec_ref, hash=targets[0].target_spec_hash), method=OriginalRef(ref=identity.method_ref, hash=identity.method_hash), evaluation_protocol=OriginalRef(ref=protocol.id, hash=protocol.payload_hash))

    def read_outcome(self, source_id):
        originals = self.service.read_outcome(source_id)
        value = originals['outcome'].model_dump(mode='json', by_alias=True)
        return originals | {'source_identity': self._identity(value, trade=True)}

    def read_observation(self, source_id):
        originals = self.service.read_observation(source_id)
        value = originals['observation']
        if originals.get('is_trade_outcome') is not False or value.get('is_trade_outcome') is not False:
            raise ValueError('learning_observation_must_remain_nontrade')
        return originals | {'source_identity': self._identity(value, trade=False)}
