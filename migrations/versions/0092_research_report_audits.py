"""Durable original audit results; publication originals remain unchanged."""
import sqlalchemy as sa
from alembic import op
revision = "0092_research_report_audits"
down_revision = "0091_research_publication_bundles"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("research_report_audit_jobs",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        *[sa.Column(name, sa.String(size), nullable=False) for name, size in (
            ("run_id",128),("request_key",64),("owner_id",128),("identity_hash",64),
            ("receipt_hash",64),("epoch",64),("state",24))],
        sa.Column("identity_json", sa.Text(), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=False),
        sa.Column("active_slot", sa.String(16), nullable=True, unique=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=False) for name in ("created_at","updated_at")],
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=True) for name in
          ("started_at","deadline_at","finished_at","native_exit_observed_at")],
        sa.Column("native_exited", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("result_bytes", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(160), nullable=True),
        sa.UniqueConstraint("run_id","request_key",name="uq_report_audit_request"),
        sa.CheckConstraint("native_exited IN (0,1)",name="ck_report_audit_native_exited"))
    op.create_index("ix_research_report_audit_jobs_run_id", "research_report_audit_jobs", ["run_id"])
    op.create_index("ix_report_audit_claim", "research_report_audit_jobs", ["state","created_at","audit_id"])
    op.create_table("research_report_audit_confirmations",
        sa.Column("audit_id",sa.String(64),primary_key=True),
        *[sa.Column(name,sa.String(64),nullable=False) for name in ("identity_hash","receipt_hash","result_hash")],
        sa.Column("result_bytes",sa.Integer(),nullable=False),
        sa.Column("confirmed_at",sa.DateTime(timezone=True),nullable=False))

def downgrade():
    # Explicit operator downgrade must not silently destroy durable evidence.
    connection = op.get_bind()
    for table in ("research_report_audit_jobs", "research_report_audit_confirmations"):
        if connection.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first() is not None:
            raise RuntimeError("report_audit_downgrade_nonempty_forbidden")
    op.drop_table("research_report_audit_confirmations")
    op.drop_index("ix_report_audit_claim",table_name="research_report_audit_jobs")
    op.drop_index("ix_research_report_audit_jobs_run_id",table_name="research_report_audit_jobs")
    op.drop_table("research_report_audit_jobs")
