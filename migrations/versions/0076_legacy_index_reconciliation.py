"""Add omitted legacy ORM indexes without changing rows or existing indexes."""

import sqlalchemy as sa
from alembic import op

revision = "0076_legacy_index_reconciliation"
down_revision = "0075_research_quality_sidecars"
branch_labels = None
depends_on = None


# These declarations reconcile 0016, 0027 and 0040 with the current ORM.
# Keep the original unique constraints and all original index names intact.
_INDEXES = (
    ("ix_evolution_jobs_trigger_key", "evolution_jobs", ("trigger_key",), True),
    (
        "ix_research_document_blocks_content_hash",
        "research_document_blocks",
        ("content_hash",),
        False,
    ),
    (
        "ix_research_document_corrections_quality_status",
        "research_document_corrections",
        ("quality_status",),
        False,
    ),
    (
        "ix_research_document_versions_correction_status",
        "research_document_versions",
        ("correction_status",),
        False,
    ),
    (
        "ix_research_document_versions_created_at",
        "research_document_versions",
        ("created_at",),
        False,
    ),
    (
        "ix_research_tool_call_reservations_status",
        "research_tool_call_reservations",
        ("status",),
        False,
    ),
)


def _matches(index: dict, columns: tuple[str, ...], unique: bool) -> bool:
    return (
        tuple(index["column_names"]) == columns
        and bool(index["unique"]) == unique
        and index.get("dialect_options", {}).get("sqlite_where") is None
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    missing = []
    # Validate every declaration before issuing any DDL. Existing mismatches
    # fail rather than deleting, renaming or replacing an original index.
    for name, table, columns, unique in _INDEXES:
        if table not in tables:
            raise RuntimeError(f"legacy_index_table_missing:{table}")
        available = {column["name"] for column in inspector.get_columns(table)}
        if not set(columns).issubset(available):
            raise RuntimeError(f"legacy_index_column_missing:{name}")
        existing = next(
            (index for index in inspector.get_indexes(table) if index["name"] == name),
            None,
        )
        if existing is None:
            missing.append((name, table, columns, unique))
        elif not _matches(existing, columns, unique):
            raise RuntimeError(f"legacy_index_definition_conflict:{name}")
    for name, table, columns, unique in missing:
        op.create_index(name, table, list(columns), unique=unique)
    fresh = sa.inspect(bind)
    for name, table, columns, unique in _INDEXES:
        actual = next(
            (index for index in fresh.get_indexes(table) if index["name"] == name),
            None,
        )
        if actual is None or not _matches(actual, columns, unique):
            raise RuntimeError(f"legacy_index_reconciliation_failed:{name}")


def downgrade() -> None:
    raise RuntimeError("legacy_index_history_downgrade_forbidden")
