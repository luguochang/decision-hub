"""Add immutable document, correction, claim and research-scope records."""

import sqlalchemy as sa
from alembic import op

revision = "0040_research_library"
down_revision = "0039_evidence_acquisition_receipt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_documents",
        sa.Column("document_id", sa.String(128), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_profile_id", sa.String(256), nullable=True),
        sa.Column("author", sa.String(256), nullable=True),
        sa.Column("market_scopes_json", sa.Text(), nullable=False),
        sa.Column("instrument_scopes_json", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False, server_default="private"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_version_id", sa.String(128), nullable=True),
    )
    op.create_index("ix_research_documents_created_at", "research_documents", ["created_at"])

    op.create_table(
        "research_document_versions",
        sa.Column("version_id", sa.String(128), primary_key=True),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("original_file_ref", sa.String(2048), nullable=False),
        sa.Column("original_file_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        sa.Column("correction_status", sa.String(32), nullable=False, server_default="not_requested"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_version_id", sa.String(128), nullable=True),
        sa.Column("parse_receipt_ref", sa.String(2048), nullable=True),
        sa.Column("corrected_content_ref", sa.String(2048), nullable=True),
        sa.Column("block_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_document_versions_document_id", "research_document_versions", ["document_id"])
    op.create_index("ix_research_document_versions_status", "research_document_versions", ["status"])
    op.create_index("ix_research_document_versions_original_file_hash", "research_document_versions", ["original_file_hash"])
    op.create_index("ix_research_document_versions_received_at", "research_document_versions", ["received_at"])

    op.create_table(
        "research_document_blocks",
        sa.Column("block_id", sa.String(160), primary_key=True),
        sa.Column("version_id", sa.String(128), nullable=False),
        sa.Column("block_type", sa.String(32), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("locator", sa.String(2048), nullable=False),
        sa.Column("bbox_json", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("has_numeric_content", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_research_document_blocks_version_id", "research_document_blocks", ["version_id"])
    op.create_index("ix_research_document_blocks_page_no", "research_document_blocks", ["page_no"])
    op.create_index("ix_research_document_blocks_needs_review", "research_document_blocks", ["needs_review"])

    op.create_table(
        "research_document_corrections",
        sa.Column("correction_id", sa.String(160), primary_key=True),
        sa.Column("version_id", sa.String(128), nullable=False),
        sa.Column("correction_model_ref", sa.String(256), nullable=False),
        sa.Column("prompt_ref", sa.String(256), nullable=False),
        sa.Column("corrected_content_ref", sa.String(2048), nullable=True),
        sa.Column("quality_status", sa.String(32), nullable=False),
        sa.Column("changes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("unresolved_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_document_corrections_version_id", "research_document_corrections", ["version_id"])

    op.create_table(
        "analyst_claims",
        sa.Column("claim_id", sa.String(160), primary_key=True),
        sa.Column("version_id", sa.String(128), nullable=False),
        sa.Column("claim_type", sa.String(32), nullable=False),
        sa.Column("stance", sa.String(32), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("supporting_quote", sa.Text(), nullable=False),
        sa.Column("locator_refs_json", sa.Text(), nullable=False),
        sa.Column("market_scope", sa.String(64), nullable=False),
        sa.Column("instrument_scope_json", sa.Text(), nullable=False),
        sa.Column("horizon", sa.String(64), nullable=True),
        sa.Column("conditions_json", sa.Text(), nullable=False),
        sa.Column("invalidation_conditions_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column("attribution_confidence", sa.String(32), nullable=False),
        sa.Column("requires_independent_evidence", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_reason", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analyst_claims_version_id", "analyst_claims", ["version_id"])
    op.create_index("ix_analyst_claims_market_scope", "analyst_claims", ["market_scope"])
    op.create_index("ix_analyst_claims_status", "analyst_claims", ["status"])

    op.create_table(
        "research_run_scopes",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("research_family", sa.String(64), nullable=False),
        sa.Column("domain_pack_ref", sa.String(128), nullable=False),
        sa.Column("instrument_scope_json", sa.Text(), nullable=False),
        sa.Column("library_context_mode", sa.String(32), nullable=False),
        sa.Column("selected_version_ids_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_run_scopes_research_family", "research_run_scopes", ["research_family"])


def downgrade() -> None:
    op.drop_index("ix_research_run_scopes_research_family", table_name="research_run_scopes")
    op.drop_table("research_run_scopes")
    op.drop_index("ix_analyst_claims_status", table_name="analyst_claims")
    op.drop_index("ix_analyst_claims_market_scope", table_name="analyst_claims")
    op.drop_index("ix_analyst_claims_version_id", table_name="analyst_claims")
    op.drop_table("analyst_claims")
    op.drop_index("ix_research_document_corrections_version_id", table_name="research_document_corrections")
    op.drop_table("research_document_corrections")
    op.drop_index("ix_research_document_blocks_needs_review", table_name="research_document_blocks")
    op.drop_index("ix_research_document_blocks_page_no", table_name="research_document_blocks")
    op.drop_index("ix_research_document_blocks_version_id", table_name="research_document_blocks")
    op.drop_table("research_document_blocks")
    op.drop_index("ix_research_document_versions_received_at", table_name="research_document_versions")
    op.drop_index("ix_research_document_versions_original_file_hash", table_name="research_document_versions")
    op.drop_index("ix_research_document_versions_status", table_name="research_document_versions")
    op.drop_index("ix_research_document_versions_document_id", table_name="research_document_versions")
    op.drop_table("research_document_versions")
    op.drop_index("ix_research_documents_created_at", table_name="research_documents")
    op.drop_table("research_documents")
