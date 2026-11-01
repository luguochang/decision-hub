"""Source authority stays in the existing W6 reader; no duplicate accounting."""
from dataclasses import dataclass
from packages.kernel.decision_hub_kernel.ports.research_learning import OriginalRef
from packages.kernel.decision_hub_kernel.application.research_learning_sources import ActionLearningSources
from packages.kernel.decision_hub_kernel.persistence.original_preparation import prepare_originals

@dataclass(frozen=True)
class PreparedTrialSource:
    source: object
    identity_read: object
    identity: object
    bodies: dict
    available_at: object

    def verify(self, session):
        self.source.verify(session)
        self.identity_read.verify(session)

    def verify_after_writes(self, session):
        self.source.verify_after_writes(session)
        self.identity_read.verify_after_writes(session)

class ActionTrialSourceReader:

    def __init__(self, execution):
        self.execution, self.database = (execution, execution.database)

    def prepare(self, source_kind: str, source_id: str):
        if source_kind != 'action_outcome':
            raise ValueError('trial_observation_preparation_not_available')
        prepared = self.execution.prepare_outcome(source_id)
        result = prepared.value
        outcome, receipt = (result['outcome'], result['execution_receipt'])
        if outcome.settled_at is None and receipt.status not in {'no_fill', 'rejected', 'unknown'}:
            raise ValueError('trial_source_not_terminal')
        if result['financial_qualified'] is not False:
            raise ValueError('trial_source_qualification_mismatch')
        value = outcome.model_dump(mode='json', by_alias=True)
        identity_read = prepare_originals(self.database, lambda session: ActionLearningSources(self.execution)._identity_in_session(session, value, trade=True))
        identity = identity_read.value
        if identity.source != OriginalRef(ref=source_id, hash=outcome.payload_hash):
            raise ValueError('trial_source_identity_mismatch')
        bodies = {'outcome': value, 'execution_receipt': receipt.model_dump(mode='json', by_alias=True), 'source_identity': identity.model_dump(mode='json')}
        if 'research_originals' in result:
            bodies['research_originals'] = {name: item.model_dump(mode='json', by_alias=True) if hasattr(item, 'model_dump') else item for name, item in result['research_originals'].items()}
        return PreparedTrialSource(prepared, identity_read, identity, bodies, result['available_at'])
