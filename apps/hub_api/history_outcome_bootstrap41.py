"""Finite qualified forecast sidecar on the same signed owner and exact grants."""

import hashlib
import os
from pathlib import Path

from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.history_outcome_v2 import (
    HistoryOutcomeV2Service,
)
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeProtocolV2Record as Protocol,
)

from .history_outcome_bootstrap import _pinned_body
from .history_outcome_routes import OutcomeBootstrapV2
from .history_product_bootstrap import CONFIG_ENV as HISTORY_CONFIG_ENV
from .history_product_bootstrap41 import HistoryProductBootstrap41, configured_history_product41

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def configured_history_outcome41(database, *, history41=None):
    pinned = _pinned_body()
    if pinned is None:
        return None
    path, resolved, body, config = pinned
    parent = Path(os.getenv(HISTORY_CONFIG_ENV, ""))
    if (
        not parent.is_absolute()
        or not parent.is_file()
        or hashlib.sha256(parent.read_bytes()).hexdigest() != config.history_config_sha256
    ):
        raise ValueError("history_outcome_history_private_config_binding_invalid")
    boot = configured_history_product41(database) if history41 is None else history41
    if type(boot) is not HistoryProductBootstrap41:
        raise ValueError("history_outcome_actual_qualified_bootstrap_required")
    boot.__post_init__()
    if (
        boot.legacy.authority.owner_identity != config.owner_identity
        or boot.legacy.originals.fixture_mode
        or boot.legacy.originals.commands.database is not database
    ):
        raise ValueError("history_outcome_private_owner_or_database_binding_invalid")
    from apps.hub_worker.history_hourly41 import HistoryHourly41

    service = HistoryOutcomeV2Service(
        boot.legacy.forecasts, hourly=HistoryHourly41(boot), qualified_forecasts=boot.forecasts
    )
    if config.protocol_ids:
        with service.commands._read_session() as session:
            rows = session.execute(
                select(Protocol.protocol_id, Protocol.owner_claim, Protocol.fixture_mode).where(
                    Protocol.protocol_id.in_(config.protocol_ids)
                )
            ).all()
            if len(rows) != len(config.protocol_ids) or any(
                owner != config.owner_identity or fixture is not False for _, owner, fixture in rows
            ):
                raise ValueError("history_outcome_granted_protocol_owner_or_fixture_invalid")
    if (
        path.resolve(strict=True) != resolved
        or resolved.read_bytes() != body
        or hashlib.sha256(parent.read_bytes()).hexdigest() != config.history_config_sha256
    ):
        raise ValueError("history_outcome_private_config_changed_during_composition")
    boot.__post_init__()
    return OutcomeBootstrapV2(boot.legacy, service, config.protocol_ids)
