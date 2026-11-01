from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from packages.kernel.decision_hub_kernel.persistence import (
    event_coverage_records,  # noqa: F401
    event_monitor_records,  # noqa: F401
    event_outcome_records,  # noqa: F401
    event_realization_records,  # noqa: F401
    event_scenario_records,  # noqa: F401
    action_execution_records,  # noqa: F401
    action_quote_repository,  # noqa: F401
    action_quote_sampling_records,  # noqa: F401
    action_records,  # noqa: F401
    decision_settings_records,  # noqa: F401
    history_dataset_forecast_v2_records,  # noqa: F401
    history_dataset_model_v2_records,  # noqa: F401
    history_dataset_v2_records,  # noqa: F401
    history_missing_resume_v2_records,  # noqa: F401
    history_observed_forecast_records,  # noqa: F401
    history_observed_records,  # noqa: F401
    market_case_records,  # noqa: F401
    path_forecast_records,  # noqa: F401
    research_publication_bundle_records,  # noqa: F401
    research_quality_records,  # noqa: F401
    research_learning_records,  # noqa: F401
    research_skill_behavior,  # noqa: F401
    research_method_trial,  # noqa: F401
    research_method_trial_selection,  # noqa: F401
    research_skill_callback_jobs,  # noqa: F401
    risk_records,  # noqa: F401
    risk_research_records,  # noqa: F401
    run_spot_admission,  # noqa: F401
    spot_scope_admission,  # noqa: F401
)
from packages.kernel.decision_hub_kernel.persistence.db import Base

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        if not isinstance(supplied_connection, Connection):
            raise TypeError("migration_connection_must_be_sqlalchemy_connection")
        # Preserve the caller's physical writer and transaction. Alembic detects
        # an external transaction and leaves commit/rollback to that caller.
        run_migrations_connection(supplied_connection)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        run_migrations_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
