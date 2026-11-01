"""Add B4 case originals, independent component pointers and market admissions."""

import sqlalchemy as sa
from alembic import op

revision = "0079_market_case_and_components"
down_revision = "0078_path_forecast_ledger"
branch_labels = None
depends_on = None


def _text(name):
    return sa.Column(name, sa.Text(), nullable=False)


def _id(name, length=128, *, nullable=False):
    return sa.Column(name, sa.String(length), nullable=nullable)


def _time(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "market_cases",
        _id("case_id"),
        _id("request_id", 256),
        _id("outlook_id"),
        _id("market", 64),
        _id("instrument"),
        _id("horizon", 32),
        _id("recipe_hash", 64, nullable=True),
        _time("created_at"),
        _time("available_at"),
        _text("payload_json"),
        _id("payload_hash", 64),
        sa.PrimaryKeyConstraint("case_id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_market_cases_outlook_id", "market_cases", ["outlook_id"])
    op.create_index(
        "ix_market_case_pit", "market_cases", ["market", "instrument", "horizon", "available_at"]
    )
    op.create_table(
        "market_case_relations",
        _id("relation_id"),
        _id("request_id", 256),
        _id("left_case_id"),
        _id("right_case_id"),
        _id("kind", 32),
        _time("created_at"),
        _text("payload_json"),
        _id("payload_hash", 64),
        sa.PrimaryKeyConstraint("relation_id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_market_case_relations_right_case_id", "market_case_relations", ["right_case_id"]
    )
    op.create_index(
        "ix_market_case_relation_left", "market_case_relations", ["left_case_id", "created_at"]
    )
    op.create_table(
        "market_component_versions",
        _id("version_id"),
        _id("kind", 32),
        _id("component_ref"),
        _id("scope_id", 192),
        _time("created_at"),
        _text("payload_json"),
        _id("payload_hash", 64),
        sa.PrimaryKeyConstraint("version_id"),
    )
    op.create_index(
        "ix_market_component_scope",
        "market_component_versions",
        ["kind", "component_ref", "scope_id"],
    )
    op.create_table(
        "market_component_shadows",
        _id("trial_id"),
        _id("version_id"),
        _time("registered_at"),
        _text("payload_json"),
        _id("payload_hash", 64),
        sa.PrimaryKeyConstraint("trial_id"),
    )
    op.create_index(
        "ix_market_component_shadows_version_id", "market_component_shadows", ["version_id"]
    )
    op.create_table(
        "market_component_reviews",
        _id("review_id"),
        _id("request_id", 256),
        _id("version_id"),
        _id("trial_id"),
        _time("reviewed_at"),
        _text("payload_json"),
        _id("payload_hash", 64),
        sa.PrimaryKeyConstraint("review_id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_market_component_reviews_version_id", "market_component_reviews", ["version_id"]
    )
    op.create_table(
        "market_component_pointers",
        _id("pointer_id", 64),
        _id("kind", 32),
        _id("component_ref"),
        _id("scope_id", 192),
        _id("version_id"),
        sa.Column("generation", sa.Integer(), nullable=False),
        _time("changed_at"),
        _id("audit_id"),
        sa.PrimaryKeyConstraint("pointer_id"),
        sa.UniqueConstraint(
            "kind", "component_ref", "scope_id", name="uq_market_component_pointer"
        ),
        sa.CheckConstraint("generation >= 1", name="ck_market_component_generation"),
    )
    op.create_table(
        "market_component_audits",
        _id("audit_id"),
        _id("request_id", 256),
        _id("pointer_id", 64),
        _time("created_at"),
        _text("payload_json"),
        _id("payload_hash", 64),
        sa.PrimaryKeyConstraint("audit_id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_market_component_audits_pointer_id", "market_component_audits", ["pointer_id"]
    )
    op.create_table(
        "market_extension_registrations",
        _id("registration_id"),
        _id("request_id", 256),
        _id("scope_id", 192),
        _time("registered_at"),
        _text("manifest_json"),
        _id("manifest_hash", 64),
        sa.PrimaryKeyConstraint("registration_id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_market_extension_registrations_scope_id", "market_extension_registrations", ["scope_id"]
    )
    op.create_table(
        "market_extension_admissions",
        _id("admission_id"),
        _id("registration_id"),
        _time("checked_at"),
        _text("payload_json"),
        _id("payload_hash", 64),
        sa.PrimaryKeyConstraint("admission_id"),
    )
    op.create_index(
        "ix_market_extension_admissions_registration_id",
        "market_extension_admissions",
        ["registration_id"],
    )


def downgrade() -> None:
    raise RuntimeError("market_case_history_downgrade_forbidden_use_component_pointer_rollback")
