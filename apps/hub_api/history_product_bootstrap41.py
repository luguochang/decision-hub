"""Pinned qualification policy on the existing signed-owner history bootstrap."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from .history_product_bootstrap39 import configured_history_product39
from .history_product_routes import HistoryProductBootstrap

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch41 import (
        HistoryForecastDispatch41Service,
    )
    from packages.kernel.decision_hub_kernel.application.history_model_qualification_v3 import (
        HistoryModelQualificationV3Service,
    )

CONFIG_ENV = "DECISION_HUB_HISTORY_QUALIFICATION_PRIVATE_CONFIG"
CONFIG_HASH_ENV = CONFIG_ENV + "_SHA256"
SCOPES = frozenset(("crypto:spot:okx:BTC-USDT:trade:1m", "crypto:spot:okx:ETH-USDT:trade:1m"))
LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ModelId = Annotated[StrictStr, Field(pattern=r"^hfit2_[a-f0-9]{48}$")]
QualificationId = Annotated[StrictStr, Field(pattern=r"^hqual3_[a-f0-9]{48}$")]


class HistoryQualificationPrivateConfig41(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    publication_grants: dict[StrictStr, ModelId] = Field(max_length=2)
    serving_grants: dict[StrictStr, QualificationId] = Field(max_length=2)

    @field_validator("publication_grants", "serving_grants")
    @classmethod
    def spot_only(cls, value):
        if set(value) - SCOPES:
            raise ValueError("history41_exact_spot_grants_required")
        return dict(value)


def _policy41():
    configured = os.getenv(CONFIG_ENV)
    if configured is None:
        return None
    expected = os.getenv(CONFIG_HASH_ENV)
    if expected is None or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise ValueError("history41_pinned_policy_sha256_required")
    path = Path(configured)
    if not configured.strip() or not path.is_absolute():
        raise ValueError("history41_absolute_policy_file_required")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("history41_existing_policy_file_required")
    with resolved.open("rb") as handle:
        body = handle.read(32769)
    if len(body) > 32768 or hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("history41_policy_file_hash_mismatch")
    try:
        policy = HistoryQualificationPrivateConfig41.model_validate_json(body)
    except ValueError:
        raise ValueError("history41_private_policy_invalid") from None
    return resolved, expected, policy


@dataclass(frozen=True)
class HistoryProductBootstrap41:
    legacy: HistoryProductBootstrap
    qualifications: HistoryModelQualificationV3Service
    forecasts: HistoryForecastDispatch41Service
    policy_path: Path
    policy_sha256: str
    publication_grants: tuple[tuple[str, str], ...]
    serving_grants: tuple[tuple[str, str], ...]

    def __post_init__(self):
        from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch41 import (
            HistoryForecastDispatch41Service,
        )
        from packages.kernel.decision_hub_kernel.application.history_model_qualification_v3 import (
            HistoryModelQualificationV3Service,
        )
        from packages.kernel.decision_hub_kernel.application.history_qualified_forecast_v3 import (
            HistoryQualifiedForecastV3Service,
        )

        if (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or HistoryProductBootstrap41.__post_init__ is not LOADED_BOOTSTRAP_INIT
            or LOADED_BOOTSTRAP_INIT.__code__ is not LOADED_BOOTSTRAP_CODE
        ):
            raise ValueError("history41_loaded_bootstrap_changed")
        if type(self.legacy) is not HistoryProductBootstrap:
            raise ValueError("history41_actual_legacy_bootstrap_required")
        self.legacy.__post_init__()
        if type(self.qualifications) is not HistoryModelQualificationV3Service or (
            type(self.forecasts) is not HistoryForecastDispatch41Service
            or type(self.forecasts.writer) is not HistoryQualifiedForecastV3Service
            or self.forecasts.writer.qualifications is not self.qualifications
            or self.qualifications.forecasts is not self.legacy.forecasts
            or self.forecasts.originals is not self.legacy.originals
            or self.forecasts.owner_claim != self.legacy.authority.owner_identity
            or self.forecasts.commands is not self.legacy.originals.commands
            or self.forecasts.grants != self.legacy.forecasts.grants
        ):
            raise ValueError("history41_trusted_executor_binding_invalid")
        if self.publication_grants != tuple(
            sorted(self.qualifications.publication_grants.items())
        ) or (self.serving_grants != tuple(sorted(self.qualifications.serving_grants.items()))):
            raise ValueError("history41_trusted_grants_changed")
        if any(
            self.forecasts.grants.get(scope) != model for scope, model in self.publication_grants
        ) or set(dict(self.serving_grants)) - set(self.forecasts.grants):
            raise ValueError("history41_grants_outside_signed_parent_resources")
        with self.policy_path.open("rb") as handle:
            body = handle.read(32769)
        if len(body) > 32768 or hashlib.sha256(body).hexdigest() != self.policy_sha256:
            raise ValueError("history41_pinned_policy_changed")

    @property
    def authority(self):
        return self.legacy.authority

    @property
    def originals(self):
        return self.legacy.originals

    @property
    def allow_producer(self):
        return self.legacy.allow_producer


LOADED_BOOTSTRAP_INIT = HistoryProductBootstrap41.__post_init__
LOADED_BOOTSTRAP_CODE = LOADED_BOOTSTRAP_INIT.__code__


def configured_history_product41(database, *, legacy=None):
    pinned = _policy41()
    if pinned is None:
        return None
    path, digest, policy = pinned
    boot = configured_history_product39(database) if legacy is None else legacy
    if type(boot) is not HistoryProductBootstrap or boot.forecasts is None:
        raise ValueError("history41_trusted_history_models_required")
    boot.__post_init__()
    if boot.originals.fixture_mode or boot.originals.commands.database is not database:
        raise ValueError("history41_actual_public_database_required")
    for scope, model in policy.publication_grants.items():
        if boot.forecasts.grants.get(scope) != model:
            raise ValueError("history41_publication_model_not_server_granted")
    if set(policy.serving_grants) - set(boot.forecasts.grants):
        raise ValueError("history41_serving_scope_not_server_granted")
    from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch41 import (
        HistoryForecastDispatch41Service,
    )
    from packages.kernel.decision_hub_kernel.application.history_model_qualification_v3 import (
        HistoryModelQualificationV3Service,
    )
    from packages.kernel.decision_hub_kernel.application.history_qualified_forecast_v3 import (
        HistoryQualifiedForecastV3Service,
    )

    qualifications = HistoryModelQualificationV3Service(
        boot.forecasts,
        publication_grants=policy.publication_grants,
        serving_grants=policy.serving_grants,
    )
    forecasts = HistoryForecastDispatch41Service(HistoryQualifiedForecastV3Service(qualifications))
    return HistoryProductBootstrap41(
        boot,
        qualifications,
        forecasts,
        path,
        digest,
        tuple(sorted(policy.publication_grants.items())),
        tuple(sorted(policy.serving_grants.items())),
    )
