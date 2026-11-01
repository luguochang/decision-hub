"""Learning/behavior/trial contracts from the single canonical schema."""
from datetime import datetime
from typing import Annotated
from pydantic import Field
from packages.contracts_py.decision_hub_contracts.research_learning_contract_base import ResearchLearningContract as Frozen
from packages.contracts_py.decision_hub_contracts.generated.research_learning import (
    BehaviorAttempt,
    BehaviorCase,
    BehaviorOriginalRef,
    BehaviorOutput,
    BehaviorProtocol,
    BehaviorReceipt,
    LearningAgentRequest,
    LearningBehaviorSuite,
    LearningBehaviorSuiteCase,
    LearningBinding,
    LearningContextPayload,
    LearningDispatchPayload,
    LearningPackagePayload,
    LearningParentRegistration,
    LearningParentRelationship,
    LearningProtocol,
    LearningRegistration,
    LearningResourceBody,
    LearningSourceIdentity,
    LearningSourceSemantics,
    LearningSourceSnapshot,
    LearningStageReceipt,
    LearningUnit,
    MethodBehaviorResponse,
    MethodTrialProtocol,
    MethodTrialReceipt,
    OriginalRef,
    ReflectionDelta,
    ReflectionHypothesis,
    ResourceChange,
)

FrozenModel = Frozen
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identity = Annotated[str, Field(min_length=1, max_length=200)]

def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("learning_aware_time_required")
    return value
