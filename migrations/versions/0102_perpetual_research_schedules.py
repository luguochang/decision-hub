"""Perpetual periodic config and explicit existing admission request protocol."""

import sqlalchemy as sa
from alembic import op

revision = "0102_perpetual_research_schedules"
down_revision = "0101_decision_opportunity_version"
branch_labels = None
depends_on = None


def upgrade():
    # 0080 historically creates this table from the current ORM. Fresh installs
    # can therefore already have this additive column before reaching 0102.
    # Persisted 0101 databases have the old shape. Authenticate both paths;
    # never reinterpret pre-versioned requests or change historical migration.
    existing = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("risk_research_requests")
    }
    if "protocol_version" in existing:
        column = existing["protocol_version"]
        if (
            not isinstance(column["type"], sa.String)
            or column["type"].length != 80
            or column["nullable"]
            or str(column["default"]).strip("'\"() ") != "risk-research-request.spot.v1"
            or op.get_bind()
            .execute(
                sa.text(
                    "SELECT count(*) FROM risk_research_requests WHERE protocol_version IS NULL OR protocol_version != 'risk-research-request.spot.v1'"
                )
            )
            .scalar_one()
        ):
            raise RuntimeError("perpetual_schedule_early_protocol_column_invalid")
    else:
        op.add_column(
            "risk_research_requests",
            sa.Column(
                "protocol_version",
                sa.String(80),
                nullable=False,
                server_default="risk-research-request.spot.v1",
            ),
        )
    op.create_table(
        "perpetual_research_schedules",
        sa.Column("schedule_id", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("partition_json", sa.Text(), nullable=False),
        sa.Column("horizon", sa.String(8), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('active','paused')", name="ck_perp_schedule_state"),
        sa.CheckConstraint("state_version >= 1", name="ck_perp_schedule_version"),
    )
    op.create_index(
        "ix_perp_schedule_due",
        "perpetual_research_schedules",
        ["state", "next_due_at", "schedule_id"],
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_research_request_protocol_immutable BEFORE UPDATE OF protocol_version ON risk_research_requests WHEN NEW.protocol_version != OLD.protocol_version BEGIN SELECT RAISE(ABORT, 'research_request_protocol_immutable'); END"
        )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM risk_research_requests WHERE protocol_version != 'risk-research-request.spot.v1'"
            )
        )
        .scalar_one()
    ):
        raise RuntimeError("perpetual_schedule_restore_required_before_downgrade")
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM perpetual_research_schedules"))
        .scalar_one()
    ):
        raise RuntimeError("perpetual_schedule_restore_required_before_downgrade")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_research_request_protocol_immutable")
    op.drop_index("ix_perp_schedule_due", "perpetual_research_schedules")
    op.drop_table("perpetual_research_schedules")
    op.drop_column("risk_research_requests", "protocol_version")
