"""Read-only trial authority composition shared by API and the research worker."""

from __future__ import annotations

from packages.kernel.decision_hub_kernel.application.research_learning_jobs import LearningJobs
from packages.kernel.decision_hub_kernel.application.research_learning_sources import (
    ActionLearningSources,
)
from packages.kernel.decision_hub_kernel.application.research_method_trial import (
    TrialAdmissionReader,
)
from packages.kernel.decision_hub_kernel.application.research_method_trial_materials import (
    TrialMaterialReader,
)
from packages.kernel.decision_hub_kernel.application.research_method_trial_sources import (
    ActionTrialSourceReader,
)
from packages.kernel.decision_hub_kernel.application.research_skill_behavior_reader import (
    BehaviorOriginalReader,
)
from packages.kernel.decision_hub_kernel.persistence.research_skill_behavior import BehaviorStore

from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobService

# The existing skill glob covers skill_* readers. These differently named files
# also affect admission and must be retained with the same installed compiler.
LEARNING_READER_SOURCE_FILES = (
    "apps/hub_worker/research_skill_learning_bootstrap.py",
    "packages/kernel/decision_hub_kernel/application/live_observation.py",
    "packages/kernel/decision_hub_kernel/application/research_learning_jobs.py",
    "packages/kernel/decision_hub_kernel/application/research_learning_async.py",
    "packages/kernel/decision_hub_kernel/application/research_learning_sources.py",
    "packages/kernel/decision_hub_kernel/application/research_learning_suite.py",
    "packages/kernel/decision_hub_kernel/application/research_learning_trial_protocols.py",
    "packages/kernel/decision_hub_kernel/application/research_method_trial.py",
    "packages/kernel/decision_hub_kernel/application/research_method_trial_materials.py",
    "packages/kernel/decision_hub_kernel/application/research_method_trial_sources.py",
    "packages/kernel/decision_hub_kernel/decision/research_method_trial.py",
    "packages/kernel/decision_hub_kernel/persistence/research_learning_records.py",
    "packages/kernel/decision_hub_kernel/persistence/research_learning_repository.py",
    "packages/kernel/decision_hub_kernel/persistence/research_method_trial.py",
    "packages/kernel/decision_hub_kernel/persistence/research_method_trial_selection.py",
    "packages/kernel/decision_hub_kernel/persistence/research_skill_behavior.py",
    "packages/kernel/decision_hub_kernel/ports/research_learning.py",
    "packages/kernel/decision_hub_kernel/ports/research_method_view.py",
    "packages/orchestration/langgraph/research_recipe_learning.py",
    "packages/contracts_py/decision_hub_contracts/research_learning_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/research_method_contract_relations.py",
    "packages/contracts_py/decision_hub_contracts/generated/research_learning.py",
    "packages/contracts_py/decision_hub_contracts/generated/research_skill.py",
    "packages/contracts_py/decision_hub_contracts/research_skill_contract_base.py",
    "packages/contracts_py/decision_hub_contracts/research_skill_generation_contract_relations.py",
    "contracts/schemas/research_learning.schema.yaml",
)


def _deny_behavior_write(_session, _protocol_id):
    raise ValueError("skill_trial_reader_write_forbidden")


def build_trial_admission_reader(
    database, *, packages, archive, execution, clock
) -> TrialAdmissionReader:
    """Construct readers only; no SQL, registration, job claiming or model load."""
    if packages.registry.database is not database or execution.database is not database:
        raise ValueError("skill_trial_database_mismatch")
    if archive is not packages.archive:
        raise ValueError("skill_trial_archive_mismatch")
    if not execution.enabled:
        raise ValueError("skill_trial_action_execution_required")
    if (
        execution.store.database is not database
        or execution.store.archive.root.resolve() != archive.root.resolve()
    ):
        raise ValueError("skill_trial_execution_archive_mismatch")
    jobs = EvolutionJobService(database, clock=clock)
    intake = LearningJobs(database, jobs, ActionLearningSources(execution), archive, clock=clock)
    materials = TrialMaterialReader(packages, intake)
    behavior = BehaviorOriginalReader(
        BehaviorStore(database, clock=clock, writer_fence=_deny_behavior_write), archive
    )
    return TrialAdmissionReader(
        database, materials, behavior, ActionTrialSourceReader(execution), clock=clock
    )
