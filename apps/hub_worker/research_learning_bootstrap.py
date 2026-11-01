"""Opt-in learning on the existing Evolution job/lease worker; no extra process."""
from dataclasses import dataclass
from math import ceil
import os
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.action_execution import ActionExecutionService
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.research_learning_context import LearningContextBuilder
from packages.kernel.decision_hub_kernel.application.research_learning_jobs import LearningJobs
from packages.kernel.decision_hub_kernel.application.research_learning_packages import LearningPackageBuilder
from packages.kernel.decision_hub_kernel.application.research_learning_registration import LearningRegistrationService
from packages.kernel.decision_hub_kernel.application.research_learning_scanner import LearningTriggerScanner, TerminalOutcomeScanner
from packages.kernel.decision_hub_kernel.application.research_learning_sources import ActionLearningSources
from packages.kernel.decision_hub_kernel.application.research_method_trial_materials import TrialMaterialReader
from packages.kernel.decision_hub_kernel.application.research_method_trial_sources import ActionTrialSourceReader
from packages.orchestration.langgraph.research_learning_continuation import LearningPackageContinuation
from packages.orchestration.langgraph.research_learning_trial_continuation import LearningTrialContinuation
from packages.orchestration.langgraph.research_recipe_learning import ResearchRecipeLearningHandler
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.runtime_adapters.research_reflection_runtime import ResearchReflectionRuntime
from packages.runtime_adapters.research_skill_behavior_runtime import ResearchBehaviorRuntime


def research_learning_enabled():
    value = os.getenv('DECISION_HUB_RESEARCH_LEARNING_ENABLED', '0')
    if value not in {'0', '1'}:
        raise ValueError('skill_learning_enable_flag_invalid')
    if value == '1' and any(os.getenv(name, '0') != '1' for name in (
        'DECISION_HUB_RESEARCH_SKILLS_ENABLED', 'DECISION_HUB_ACTION_EXECUTION_ENABLED',
        'DECISION_HUB_ACTION_PRODUCT_ENABLED', 'DECISION_HUB_SPOT_RESEARCH_ENABLED')):
        raise ValueError('skill_learning_action_execution_required')
    return value == '1'


def build_learning_execution(database, data_root, *, clock=None):
    if not research_learning_enabled():
        return None
    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    root = Path(data_root)
    return ActionExecutionService(database,
        market_store=MarketPathStore(database, RawBodyArchive(root / 'raw-captures')),
        feature_root=root / 'action-product/features', enabled=True, clock=clock or utcnow)


@dataclass(frozen=True)
class LearningWorkerServices:
    handler: object
    scanner: object
    registrations: object
    skills: object


def build_learning_worker_services(database, data_root, *, jobs, legacy_scanner, provider_config, clock):
    if not research_learning_enabled():
        return None
    from apps.hub_worker.research_skill_bootstrap import configured_research_skills
    execution = build_learning_execution(database, data_root, clock=clock)
    skills = configured_research_skills(database, data_root, execution=execution)
    if skills is None or skills.trial_admission is None:
        raise ValueError('learning_installed_skill_readers_required')
    intake = LearningJobs(database, jobs, ActionLearningSources(execution), skills.packages.archive, clock=clock)
    contexts = LearningContextBuilder(skills.packages, skills.contexts, clock=clock)
    handler = ResearchRecipeLearningHandler(intake, ResearchReflectionRuntime(provider_config), contexts)
    materials = TrialMaterialReader(skills.packages, intake)
    trials = LearningTrialContinuation(handler, materials, ActionTrialSourceReader(execution),
        ResearchBehaviorRuntime(provider_config, clock=clock),
        call_timeout_seconds=ceil(provider_config.timeout_seconds))
    packages = LearningPackageBuilder(skills.packages, skills.contexts,
        Path(data_root) / 'research-learning/packages', learning_archive=intake.archive)
    handler.continuation = LearningPackageContinuation(handler, packages, trials=trials)
    registrations = LearningRegistrationService(database, skills, clock=clock)
    scanner = LearningTriggerScanner(legacy_scanner, TerminalOutcomeScanner(intake), registrations=registrations)
    return LearningWorkerServices(handler, scanner, registrations, skills)
