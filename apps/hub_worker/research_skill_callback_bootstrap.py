"""Explicit opt-in; absent migration fails rather than pretending callbacks run."""

from __future__ import annotations

import os
import json
import logging
from dataclasses import asdict
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.research_skill_callbacks import (
    ResearchSkillCallbackService,
)
from packages.kernel.decision_hub_kernel.persistence.research_skill_callback_jobs import (
    ResearchSkillCallbackJobs,
)
from sqlalchemy import inspect
from packages.kernel.decision_hub_kernel.persistence.operation_phases import OperationPhases

from packages.kernel.decision_hub_kernel.application.research_skill_package import encoded, sha


def configured_research_skill_callbacks(database, skills):
    if os.getenv("DECISION_HUB_DURABLE_SKILL_CALLBACKS_ENABLED", "0") != "1":
        return None
    if skills is None:
        raise ValueError("skill_callback_skills_not_installed")
    if not inspect(database.engine).has_table("research_skill_callback_jobs"):
        raise ValueError("skill_callback_migration_required")
    root = Path(__file__).resolve().parents[2]
    files = (
        "packages/kernel/decision_hub_kernel/application/research_skill_service.py",
        "packages/kernel/decision_hub_kernel/application/research_skill_callbacks.py",
        "packages/kernel/decision_hub_kernel/persistence/research_skill_callback_jobs.py",
        "apps/hub_worker/research_skill_callbacks.py",
        "apps/hub_worker/research_skill_callback_bootstrap.py",
        "apps/hub_api/research_skill_routes.py",
    )

    def producer_identity():
        skills.packages.installed.verify()
        return sha(
            encoded(
                dict(
                    compiler_hash=skills.packages.installed.code_hash,
                    files={relative: sha((root / relative).read_bytes()) for relative in files},
                )
            )
        )

    def diagnostics_factory():
        def observe(event):
            logging.getLogger(__name__).info(
                "research_skill_callback_phase %s", json.dumps(asdict(event), separators=(",", ":"))
            )
        return OperationPhases(observe)

    def source_observer(event):
        logging.getLogger(__name__).info(
            "research_skill_callback_source_timing %s", json.dumps(event, separators=(",", ":"))
        )

    return ResearchSkillCallbackService(
        skills, ResearchSkillCallbackJobs(database), producer_identity=producer_identity,
        diagnostics_factory=diagnostics_factory, source_observer=source_observer,
    )
