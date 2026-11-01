"""Persist initial/daily/alert admission intents without a second research executor."""

from alembic import op

revision = "0080_risk_research_requests"
down_revision = "0079_market_case_and_components"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
        RiskResearchPlanRecord,
        RiskResearchRequestRecord,
        RiskResearchTriggerRecord,
    )

    for record in (RiskResearchPlanRecord, RiskResearchRequestRecord, RiskResearchTriggerRecord):
        record.__table__.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    raise RuntimeError("risk_research_audit_downgrade_forbidden")
