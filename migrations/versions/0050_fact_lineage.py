"""Immutable input lineage and bounded exact-query reuse."""

from alembic import op

revision = "0050_fact_lineage"
down_revision = "0049_study_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE raw_captures (
        capture_id VARCHAR(128) NOT NULL,
        context_id VARCHAR(256) NOT NULL,
        url VARCHAR(2048) NOT NULL,
        status_code INTEGER,
        body_hash VARCHAR(64),
        body_ref VARCHAR(256),
        byte_count INTEGER NOT NULL,
        media_type VARCHAR(128) NOT NULL,
        adapter_version VARCHAR(128) NOT NULL,
        error_code VARCHAR(128),
        started_at DATETIME NOT NULL,
        completed_at DATETIME NOT NULL,
        recorded_at DATETIME NOT NULL,
        PRIMARY KEY (capture_id)
    )
    """)
    op.execute("""
    CREATE INDEX ix_raw_captures_context_id ON raw_captures (context_id)
    """)
    op.execute("""
    CREATE TABLE parsed_versions (
        parsed_version_id VARCHAR(128) NOT NULL,
        original_evidence_id VARCHAR(128) NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        parser_version VARCHAR(256) NOT NULL,
        capture_ids_json TEXT NOT NULL,
        content_ref VARCHAR(2048),
        acquisition_receipt_id VARCHAR(128),
        archive_status VARCHAR(32) NOT NULL,
        recorded_at DATETIME NOT NULL,
        PRIMARY KEY (parsed_version_id)
    )
    """)
    op.execute("""
    CREATE TABLE evidence_parsed_links (
        evidence_id VARCHAR(128) NOT NULL,
        parsed_version_id VARCHAR(128) NOT NULL,
        PRIMARY KEY (evidence_id)
    )
    """)
    op.execute("""
    CREATE INDEX ix_evidence_parsed_links_parsed_version_id
    ON evidence_parsed_links (parsed_version_id)
    """)
    op.execute("""
    CREATE TABLE fact_versions (
        fact_version_id VARCHAR(128) NOT NULL,
        semantic_key VARCHAR(64) NOT NULL,
        market VARCHAR(64) NOT NULL,
        scope_instrument VARCHAR(128) NOT NULL,
        field VARCHAR(128) NOT NULL,
        source_id VARCHAR(256) NOT NULL,
        original_fact_id VARCHAR(128) NOT NULL,
        parsed_version_id VARCHAR(128) NOT NULL,
        payload_json TEXT NOT NULL,
        known_at DATETIME NOT NULL,
        observed_at DATETIME NOT NULL,
        recorded_at DATETIME NOT NULL,
        PRIMARY KEY (fact_version_id)
    )
    """)
    op.execute("""
    CREATE INDEX ix_fact_versions_field ON fact_versions (field)
    """)
    op.execute("""
    CREATE INDEX ix_fact_versions_parsed_version_id ON fact_versions (parsed_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_fact_versions_pit ON fact_versions (market, scope_instrument, known_at)
    """)
    op.execute("""
    CREATE INDEX ix_fact_versions_semantic ON fact_versions (semantic_key, known_at)
    """)
    op.execute("""
    CREATE INDEX ix_fact_versions_source_id ON fact_versions (source_id)
    """)
    op.execute("""
    CREATE TABLE run_input_refs (
        fact_id VARCHAR(128) NOT NULL,
        run_id VARCHAR(128) NOT NULL,
        fact_version_id VARCHAR(128) NOT NULL,
        use_mode VARCHAR(32) NOT NULL,
        as_of DATETIME NOT NULL,
        adopted_at DATETIME NOT NULL,
        PRIMARY KEY (fact_id)
    )
    """)
    op.execute("""
    CREATE INDEX ix_run_input_refs_fact_version_id ON run_input_refs (fact_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_run_input_refs_run_id ON run_input_refs (run_id)
    """)
    op.execute("""
    CREATE TABLE capability_reuse_batches (
        batch_id VARCHAR(128) NOT NULL,
        query_key VARCHAR(64) NOT NULL,
        run_id VARCHAR(128) NOT NULL,
        result_json TEXT NOT NULL,
        fact_versions_json TEXT NOT NULL,
        parsed_versions_json TEXT NOT NULL,
        known_at DATETIME NOT NULL,
        expires_at DATETIME NOT NULL,
        PRIMARY KEY (batch_id)
    )
    """)
    op.execute("""
    CREATE INDEX ix_capability_reuse_batches_query_key ON capability_reuse_batches (query_key)
    """)


def downgrade():
    op.drop_table("capability_reuse_batches")
    op.drop_table("run_input_refs")
    op.drop_table("fact_versions")
    op.drop_table("evidence_parsed_links")
    op.drop_table("parsed_versions")
    op.drop_table("raw_captures")
