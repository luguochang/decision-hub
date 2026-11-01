"""Separately pinned evaluation grants on the existing signed history owner."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator
from sqlalchemy import select

from .history_outcome_routes import OutcomeBootstrapV2
from .history_product_bootstrap import CONFIG_ENV as HISTORY_CONFIG_ENV
from .history_product_bootstrap39 import configured_history_product39
from .history_product_routes import HistoryProductBootstrap

CONFIG_ENV = "DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG"
CONFIG_HASH_ENV = "DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG_SHA256"
LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ProtocolId = Annotated[StrictStr, Field(pattern=r"^heval2_[a-f0-9]{48}$")]


class HistoryOutcomePrivateConfigV2(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)
    owner_identity: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
    history_config_sha256: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
    protocol_ids: Annotated[tuple[ProtocolId, ...], Field(max_length=16)]

    @model_validator(mode="after")
    def unique_grants(self):
        if len(set(self.protocol_ids)) != len(self.protocol_ids):
            raise ValueError("history_outcome_unique_protocol_grants_required")
        return self


def _pinned_body():
    configured = os.getenv(CONFIG_ENV)
    if configured is None:
        return None
    expected = os.getenv(CONFIG_HASH_ENV)
    if expected is None or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise ValueError("history_outcome_private_config_sha256_required")
    path = Path(configured)
    if not configured.strip() or not path.is_absolute():
        raise ValueError("history_outcome_private_absolute_config_required")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("history_outcome_private_existing_config_required")
    with resolved.open("rb") as handle:
        body = handle.read(32769)
    if len(body) > 32768 or hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("history_outcome_private_config_hash_mismatch")
    try:
        config = HistoryOutcomePrivateConfigV2.model_validate_json(body)
    except ValueError:
        raise ValueError("history_outcome_private_configuration_invalid") from None
    return path, resolved, body, config


def configured_history_outcome(database, *, history=None):
    """No provisioning, migration, fit, collection, production or protocol save."""
    pinned = _pinned_body()
    if pinned is None:
        return None
    path, resolved, body, config = pinned
    parent_path = Path(os.getenv(HISTORY_CONFIG_ENV, ""))
    if (
        not parent_path.is_absolute()
        or not parent_path.is_file()
        or (hashlib.sha256(parent_path.read_bytes()).hexdigest() != config.history_config_sha256)
    ):
        raise ValueError("history_outcome_history_private_config_binding_invalid")
    boot = configured_history_product39(database) if history is None else history
    if type(boot) is not HistoryProductBootstrap:
        raise ValueError("history_outcome_actual_history_bootstrap_required")
    boot.__post_init__()
    if (
        boot.authority.owner_identity != config.owner_identity
        or boot.originals.fixture_mode
        or (boot.originals.commands.database is not database)
    ):
        raise ValueError("history_outcome_private_owner_or_database_binding_invalid")
    from apps.hub_worker.history_hourly39 import HistoryHourly39
    from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch39 import (
        HistoryForecastDispatch39Service,
    )
    from packages.kernel.decision_hub_kernel.application.history_outcome_v2 import (
        HistoryOutcomeV2Service,
    )
    from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
        HistoryOutcomeProtocolV2Record,
    )

    if type(boot.forecasts) is not HistoryForecastDispatch39Service:
        raise ValueError("history_outcome_trusted_forecast_dispatch_required")
    service = HistoryOutcomeV2Service(boot.forecasts, hourly=HistoryHourly39(boot))
    if config.protocol_ids:
        with service.commands._read_session() as session:
            rows = session.execute(
                select(
                    HistoryOutcomeProtocolV2Record.protocol_id,
                    HistoryOutcomeProtocolV2Record.owner_claim,
                    HistoryOutcomeProtocolV2Record.fixture_mode,
                ).where(HistoryOutcomeProtocolV2Record.protocol_id.in_(config.protocol_ids))
            ).all()
            if len(rows) != len(config.protocol_ids) or any(
                owner != config.owner_identity or fixture is not False for _, owner, fixture in rows
            ):
                raise ValueError("history_outcome_granted_protocol_owner_or_fixture_invalid")
    if (
        path.resolve(strict=True) != resolved
        or resolved.read_bytes() != body
        or (hashlib.sha256(parent_path.read_bytes()).hexdigest() != config.history_config_sha256)
    ):
        raise ValueError("history_outcome_private_config_changed_during_composition")
    return OutcomeBootstrapV2(boot, service, config.protocol_ids)
