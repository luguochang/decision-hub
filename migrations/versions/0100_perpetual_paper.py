"""Internal perpetual paper originals; never migrate or reinterpret spot paper."""

from alembic import op

revision = "0100_perpetual_paper"
down_revision = "0099_decision_settings"
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL is kept inline below; no imports of mutable application models.
    op.execute(
        "CREATE TABLE perpetual_paper_revisions ("
        "revision_id VARCHAR(128) NOT NULL PRIMARY KEY, account_key VARCHAR(128) NOT NULL, "
        "state_version INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "payload_hash VARCHAR(64) NOT NULL, "
        "CONSTRAINT uq_perp_paper_revision UNIQUE (account_key, state_version), "
        "CONSTRAINT ck_perp_paper_revision_version CHECK (state_version >= 1))"
    )
    op.execute(
        "CREATE TABLE perpetual_paper_heads ("
        "account_key VARCHAR(128) NOT NULL PRIMARY KEY, owner_id VARCHAR(128) NOT NULL, "
        "account_id VARCHAR(128) NOT NULL, current_revision_id VARCHAR(128) NOT NULL, "
        "state_version INTEGER NOT NULL, "
        "CONSTRAINT uq_perp_paper_owner_account UNIQUE (owner_id, account_id), "
        "CONSTRAINT ck_perp_paper_head_version CHECK (state_version >= 1))"
    )
    op.execute(
        "CREATE TABLE perpetual_paper_quote_capacity ("
        "account_key VARCHAR(128) NOT NULL, quote_key VARCHAR(64) NOT NULL, "
        "order_side VARCHAR(4) NOT NULL, consumed_contracts VARCHAR(80) NOT NULL, "
        "state_version INTEGER NOT NULL, PRIMARY KEY (account_key, quote_key, order_side), "
        "CONSTRAINT ck_perp_paper_capacity_side CHECK (order_side IN ('buy','sell')), "
        "CONSTRAINT ck_perp_paper_capacity_version CHECK (state_version >= 1))"
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_perpetual_paper_revisions_immutable_insert "
            "BEFORE INSERT ON perpetual_paper_revisions WHEN EXISTS ("
            "SELECT 1 FROM perpetual_paper_revisions WHERE revision_id = NEW.revision_id OR "
            "(account_key = NEW.account_key AND state_version = NEW.state_version)) "
            "BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: perpetual_paper_revisions'); END"
        )
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_perpetual_paper_revisions_immutable_{operation.lower()} "
                f"BEFORE {operation} ON perpetual_paper_revisions "
                "BEGIN SELECT RAISE(ABORT, 'perpetual_paper_revisions_immutable'); END"
            )


def downgrade():
    op.drop_table("perpetual_paper_quote_capacity")
    op.drop_table("perpetual_paper_heads")
    op.drop_table("perpetual_paper_revisions")
