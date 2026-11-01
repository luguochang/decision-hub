"""Strict opt-in history launch configuration; no provisioning or execution."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.persistence.db import Database

    from .history_product_routes import HistoryProductBootstrap

CONFIG_ENV = "DECISION_HUB_HISTORY_PRIVATE_CONFIG"
CONFIG_HASH_ENV = "DECISION_HUB_HISTORY_PRIVATE_CONFIG_SHA256"
DATA_ENV = "DECISION_HUB_DATA_DIR"
SCOPES = frozenset({"crypto:spot:okx:BTC-USDT:trade:1m", "crypto:spot:okx:ETH-USDT:trade:1m"})
PlanId = Annotated[StrictStr, Field(pattern=r"^hds2_plan_[a-f0-9]{48}$")]
DatasetId = Annotated[StrictStr, Field(pattern=r"^hds2_dataset_[a-f0-9]{48}$")]
EpochId = Annotated[StrictStr, Field(pattern=r"^hds2_epoch_[a-f0-9]{48}$")]
ModelId = Annotated[StrictStr, Field(pattern=r"^hfit2_[a-f0-9]{48}$")]


class HistoryProductPrivateConfig(BaseModel):
    """This exact JSON schema is accepted only from a server-private file."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    owner_identity: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
    origin: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    signing_key_hex: SecretStr = Field(repr=False, json_schema_extra={"pattern": "^[a-f0-9]{64}$"})
    pairing_sha256_hex: SecretStr = Field(
        repr=False, json_schema_extra={"pattern": "^[a-f0-9]{64}$"}
    )
    plan_id: PlanId | None = None
    dataset_id: DatasetId | None = None
    epoch_id: EpochId | None = None
    model_grants: dict[StrictStr, ModelId] = Field(
        default_factory=dict,
        max_length=2,
        json_schema_extra={"propertyNames": {"enum": sorted(SCOPES)}},
    )
    allow_producer: StrictBool = False
    lifetime_seconds: Annotated[StrictInt, Field(ge=1, le=900)] = 600

    @field_validator("signing_key_hex", "pairing_sha256_hex", mode="before")
    @classmethod
    def private_hex(cls, value):
        if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
            raise ValueError("history_private_32byte_hex_required")
        return value

    @field_validator("origin")
    @classmethod
    def local_origin(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port is None
            or parsed.port == 0
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("history_private_loopback_origin_required")
        return value

    @field_validator("model_grants")
    @classmethod
    def exact_scopes(cls, value):
        if set(value) - SCOPES:
            raise ValueError("history_private_exact_spot_model_grants_required")
        return dict(value)

    @model_validator(mode="after")
    def explicit_resources(self):
        for kind in ("plan", "dataset", "epoch"):
            value = getattr(self, kind + "_id")
            if value is not None and re.fullmatch("hds2_" + kind + r"_[a-f0-9]{48}", value) is None:
                raise ValueError("history_private_resource_identity_invalid")
        if (self.dataset_id is not None or self.epoch_id is not None) and self.plan_id is None:
            raise ValueError("history_private_plan_grant_required")
        if self.allow_producer and not self.model_grants:
            raise ValueError("history_private_producer_model_grant_required")
        return self


def _granted_metadata(commands, config):
    """Check explicit headers only; immutable originals are verified on actual use."""
    if not any((config.plan_id, config.dataset_id, config.epoch_id, config.model_grants)):
        return
    with commands._read_session() as session:
        connection = session.connection()

        def original(identifier, kind):
            row = (
                connection.exec_driver_sql(
                    "SELECT owner_claim,kind,plan_id,"
                    "json_extract(payload_json,'$.provenance.fixture_mode') "
                    "AS fixture FROM market_history_v2_originals WHERE original_id=? LIMIT 2",
                    (identifier,),
                )
                .mappings()
                .all()
            )
            if len(row) != 1 or (row[0]["owner_claim"], row[0]["kind"], row[0]["fixture"]) != (
                config.owner_identity,
                kind,
                0,
            ):
                raise ValueError("history_private_resource_owner_or_fixture_invalid")
            if config.plan_id is not None and row[0]["plan_id"] != config.plan_id:
                raise ValueError("history_private_resource_plan_mismatch")
            return row[0]

        for kind in ("plan", "dataset", "epoch"):
            identifier = getattr(config, kind + "_id")
            if identifier is not None:
                original(identifier, kind)
        for scope, identifier in config.model_grants.items():
            rows = (
                connection.exec_driver_sql(
                    "SELECT owner_claim,scope_id,plan_id,dataset_id FROM market_history_v2_models "
                    "WHERE model_id=? LIMIT 2",
                    (identifier,),
                )
                .mappings()
                .all()
            )
            if len(rows) != 1 or (rows[0]["owner_claim"], rows[0]["scope_id"]) != (
                config.owner_identity,
                scope,
            ):
                raise ValueError("history_private_model_owner_or_scope_invalid")
            row = rows[0]
            if (config.plan_id is not None and row["plan_id"] != config.plan_id) or (
                config.dataset_id is not None and row["dataset_id"] != config.dataset_id
            ):
                raise ValueError("history_private_model_dataset_grant_mismatch")
            original(row["dataset_id"], "dataset")


def configured_history_product(database: Database) -> HistoryProductBootstrap | None:
    """Absent configuration is closed; an invalid supplied configuration fails startup."""
    configured = os.getenv(CONFIG_ENV)
    if configured is None:
        return None
    if not configured.strip():
        raise ValueError("history_private_config_path_required")
    path = Path(configured)
    if not path.is_absolute():
        raise ValueError("history_private_config_absolute_path_required")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("history_private_config_existing_file_required")
    with resolved.open("rb") as handle:
        body = handle.read(32_769)
    if len(body) > 32_768:
        raise ValueError("history_private_config_size_exceeded")
    expected_hash = os.getenv(CONFIG_HASH_ENV)
    if expected_hash is not None and (
        re.fullmatch(r"[a-f0-9]{64}", expected_hash) is None
        or hashlib.sha256(body).hexdigest() != expected_hash
    ):
        raise ValueError("history_private_launch_config_hash_mismatch")
    try:
        config = HistoryProductPrivateConfig.model_validate_json(body)
    except ValueError:
        raise ValueError("history_private_configuration_invalid") from None
    data_value = os.getenv(DATA_ENV)
    if data_value is None or not data_value.strip():
        raise ValueError("history_private_launch_data_directory_required")
    data = Path(data_value).resolve(strict=True)
    if not data.is_dir():
        raise ValueError("history_private_launch_data_directory_invalid")
    from apps.hub_worker.composition import build_history_observed_worker
    from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
        HistoryDatasetForecastV2Service,
    )
    from packages.kernel.decision_hub_kernel.application.history_dataset_model_v2 import (
        HistoryDatasetModelV2Service,
    )
    from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
        HistoryDatasetV2Service,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import Database as ActualDatabase

    from .history_owner_authority import HistoryOwnerAuthority
    from .history_product_routes import HistoryProductBootstrap

    if type(database) is not ActualDatabase or database.engine.dialect.name != "sqlite":
        raise ValueError("history_private_actual_database_required")
    commands = build_history_observed_worker(database, owner_id=config.owner_identity)
    originals = HistoryDatasetV2Service(commands, data, fixture_mode=False)
    _granted_metadata(commands, config)
    models = HistoryDatasetModelV2Service(originals)
    forecasts = (
        None
        if not config.model_grants
        else HistoryDatasetForecastV2Service(
            models, owner_claim=config.owner_identity, grants=config.model_grants
        )
    )
    authority = HistoryOwnerAuthority(
        signing_key=bytes.fromhex(config.signing_key_hex.get_secret_value()),
        pairing_sha256=bytes.fromhex(config.pairing_sha256_hex.get_secret_value()),
        owner_identity=config.owner_identity,
        origin=config.origin,
        lifetime_seconds=config.lifetime_seconds,
    )
    if (
        path.resolve(strict=True) != resolved
        or hashlib.sha256(resolved.read_bytes()).digest() != hashlib.sha256(body).digest()
    ):
        raise ValueError("history_private_config_changed_during_composition")
    if Path(os.environ[DATA_ENV]).resolve(strict=True) != data:
        raise ValueError("history_private_launch_data_directory_changed")
    return HistoryProductBootstrap(
        authority=authority,
        originals=originals,
        plan_id=config.plan_id,
        dataset_id=config.dataset_id,
        epoch_id=config.epoch_id,
        forecasts=forecasts,
        allow_producer=config.allow_producer,
    )
