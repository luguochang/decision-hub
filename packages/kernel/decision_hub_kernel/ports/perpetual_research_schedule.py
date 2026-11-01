"""Explicit internal wire for owner-authorized periodic research, never execution."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
    DecisionSourceRefV1,
)

from .perpetual_research_inputs import CurrentPaperPartition

LEGACY_PROTOCOL = "risk-research-request.perpetual-periodic.v1"
PROTOCOL = "risk-research-request.perpetual-motives.v2"
EVENT_PROTOCOL = "risk-research-request.perpetual-event.v1"
SUPPORTED_PROTOCOLS = (LEGACY_PROTOCOL, PROTOCOL, EVENT_PROTOCOL)
INTERVAL_SECONDS = 3600


class ScheduleWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PerpetualResearchScheduleCommandV1(ScheduleWire):
    schema_version: Literal["perpetual-research-schedule-command.v1"]
    request_id: str = Field(min_length=1, max_length=128)
    partition: DecisionSettingsPartitionV1
    horizon: Literal["4h", "24h", "72h"]
    expected_state_version: int = Field(ge=0, strict=True)
    operation: Literal["enable", "pause"]
    interval_seconds: Literal[3600] = 3600
    settings_binding_policy: Literal["follow_current_saved_head"] = "follow_current_saved_head"


class PerpetualResearchScheduleReceiptV1(ScheduleWire):
    schema_version: Literal["perpetual-research-schedule-receipt.v1"]
    owner_id: str
    request_id: str
    command: PerpetualResearchScheduleCommandV1
    command_hash: str
    schedule_id: str
    state_version: int = Field(ge=1)
    state: Literal["active", "paused"]
    accepted_at: AwareDatetime
    revision: DecisionSourceRefV1
    payload_hash: str


class PerpetualResearchScheduleCommandV2(PerpetualResearchScheduleCommandV1):
    schema_version: Literal["perpetual-research-schedule-command.v2"]
    partition: CurrentPaperPartition
    input_policy: Literal["saved_preferences_current_paper_ledger.v1"]


class PerpetualResearchScheduleReceiptV2(PerpetualResearchScheduleReceiptV1):
    schema_version: Literal["perpetual-research-schedule-receipt.v2"]
    command: PerpetualResearchScheduleCommandV2


def parse_perpetual_research_schedule_command(value):
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    models = {
        "perpetual-research-schedule-command.v1": PerpetualResearchScheduleCommandV1,
        "perpetual-research-schedule-command.v2": PerpetualResearchScheduleCommandV2,
    }
    if not isinstance(raw, dict) or raw.get("schema_version") not in models:
        raise ValueError("perpetual_schedule_command_version_unsupported")
    return models[raw["schema_version"]].model_validate(raw)


def parse_perpetual_research_schedule_receipt(value):
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    models = {
        "perpetual-research-schedule-receipt.v1": PerpetualResearchScheduleReceiptV1,
        "perpetual-research-schedule-receipt.v2": PerpetualResearchScheduleReceiptV2,
    }
    if not isinstance(raw, dict) or raw.get("schema_version") not in models:
        raise ValueError("perpetual_schedule_receipt_version_unsupported")
    return models[raw["schema_version"]].model_validate(raw)


class PerpetualPeriodicTriggerV1(ScheduleWire):
    schema_version: Literal["perpetual-research-periodic-trigger.v1"]
    schedule_id: str
    schedule_ref: DecisionSourceRefV1
    owner_id: str
    partition: DecisionSettingsPartitionV1
    horizon: Literal["4h", "24h", "72h"]
    scheduled_at: AwareDatetime
    deadline_at: AwareDatetime
    recorded_at: AwareDatetime
    interval_seconds: Literal[3600] = 3600


class PerpetualEventTriggerItemV1(ScheduleWire):
    event_ref: DecisionSourceRefV1
    event_key: str = Field(min_length=1, max_length=512)
    stage_id: Literal["release", "meeting_date"]


class PerpetualEventResearchTriggerV1(ScheduleWire):
    schema_version: Literal["perpetual-research-event-trigger.v1"]
    schedule_id: str
    schedule_ref: DecisionSourceRefV1
    owner_id: str
    partition: DecisionSettingsPartitionV1
    horizon: Literal["4h", "24h", "72h"]
    scheduled_at: AwareDatetime
    deadline_at: AwareDatetime
    recorded_at: AwareDatetime
    trigger_events: list[PerpetualEventTriggerItemV1] = Field(min_length=1, max_length=32)


class PerpetualEventMotiveV1(ScheduleWire):
    schema_version: Literal["perpetual-event-motive.v1"]
    schedule_ref: DecisionSourceRefV1
    owner_id: str
    partition: DecisionSettingsPartitionV1
    horizon: Literal["4h", "24h", "72h"]
    scheduled_at: AwareDatetime
    deadline_at: AwareDatetime
    event_ref: DecisionSourceRefV1
    source_id: Literal["cpi-publication-schedule", "bls-calendar", "fed-calendar"]
    event_key: str
    family: Literal["cpi", "nfp", "fomc"]
    stage_id: Literal["release", "meeting_date"]
    known_at: AwareDatetime
    recorded_at: AwareDatetime


def next_hour(value: datetime):
    from datetime import timedelta

    return value.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
