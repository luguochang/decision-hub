"""Artifact normal-preparation adapter; no second dispatch/model path."""
from dataclasses import dataclass
from packages.kernel.decision_hub_kernel.application.research_execution_binding import read_prepared_execution_in_session
from packages.kernel.decision_hub_kernel.application.research_skill_preparation import SkillPreparation, SkillPreparationRead
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import DECISION_OUTPUT_SCHEMA_REF

@dataclass(frozen=True)
class TrialPreparationRead(SkillPreparationRead):
    trial_disposition: str | None = None

class TrialAwareSkillPreparation(SkillPreparation):
    """Composition replaces only preparation, sharing the same selection service.

    Normal SkillUse/Node/Consumption/Publication readers receive that same
    TrialSkillSelectionService. Existing source-closure rules must include the
    new modules when this proposal is eventually installed.
    """

    def prepare(self, run_id, generation):
        with self.selections.packages.registry.database.session() as session:
            session.connection().exec_driver_sql('BEGIN')
            saved = read_prepared_execution_in_session(session, run_id, generation)
            request = saved.execution.request
            eligible = request.output_schema_ref == DECISION_OUTPUT_SCHEMA_REF and request.research_mode == 'current_decision' and (request.execution_mode == 'live')
            existing = self.selections._row(session, run_id)
            if eligible and existing is not None:
                self.selections.read_in_session(session, run_id)
                return TrialPreparationRead('original_selection_retained', trial_disposition='same_run_original_no_reselection')
        if not eligible or generation != 1 or saved.execution.turn_kind != 'research_round':
            return super().prepare(run_id, generation)
        status, prepared = self.selections.prepare_current(run_id)
        if status == 'existing':
            return TrialPreparationRead('original_selection_retained', trial_disposition='same_run_original_no_reselection')
        if status == 'prepared':
            return TrialPreparationRead('prepared', prepared, 'trial_registered')
        if status not in {'no_trial_pointer', 'trial_not_started', 'trial_expired', 'trial_quota_exhausted', 'trial_horizon_unsupported'}:
            raise ValueError('trial_preparation_state_unexpected')
        baseline = super().prepare(run_id, generation)
        return TrialPreparationRead(baseline.status, baseline.prepared, status)
