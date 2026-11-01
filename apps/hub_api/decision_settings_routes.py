"""Private settings transport; only signed local sessions establish the owner."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select

from apps.hub_api.local_owner_session import LocalOwnerSessionAuthority
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsCommandV1, DecisionSettingsCurrentViewV1, DecisionSettingsPartitionV1,
    AccountSnapshotV2, PositionContextV2, DecisionSettingsSaveReceiptV1, DecisionSourceRefV1, InstrumentContractV1,
)
from packages.kernel.decision_hub_kernel.application.decision_execution_profile import paper_execution_profile_reference
from packages.kernel.decision_hub_kernel.application.decision_settings import DecisionSettingsService, declared_account_id
from packages.kernel.decision_hub_kernel.application.decision_settings_binding import DecisionSettingsBindingReader
from packages.kernel.decision_hub_kernel.application.development_history_reader import DevelopmentHistoryReader
from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import OKXPerpetualSource
from packages.kernel.decision_hub_kernel.application.perpetual_paper import PerpetualPaperService
from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord, RunRoleOutputRecord, as_utc
from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_records import PerpetualPaperHeadRecord
from packages.kernel.decision_hub_kernel.ports.okx_perpetual_original import VERSION, public_url
from packages.provider_adapters.research.raw_capture import RawBodyArchive

COOKIE = "decision_hub_settings_owner"
BASE = "/v1/decision-settings"
INSTRUMENTS = ("okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP")


class Transport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, regex_engine="python-re")


class Pair(Transport):
    pairing_token: str = Field(min_length=32, max_length=2048, repr=False)


class PrepareInstrument(Transport):
    instrument_id: Literal["okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"]


class InstrumentOption(Transport):
    instrument_id: Literal["okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"]
    instrument: DecisionSourceRefV1
    contract: InstrumentContractV1
    execution_profile: DecisionSourceRefV1


class SettingsOptions(Transport):
    schema_version: Literal["decision-settings-options.v1"] = "decision-settings-options.v1"
    owner_id: str
    declared_account_id: str
    availability: Literal["available", "unavailable"]
    reason: str | None
    instruments: list[InstrumentOption]
    paper_available: bool
    paper_reason: str | None
    paper_accounts: list[PaperOption]


class PaperOption(Transport):
    partition: DecisionSettingsPartitionV1
    account_snapshot: AccountSnapshotV2
    position_context: PositionContextV2
    execution_profile: DecisionSourceRefV1


SettingsOptions.model_rebuild()


def configured_settings_authority() -> LocalOwnerSessionAuthority | None:
    """Consume private launcher material; never derive identity from a request."""
    name = os.getenv("DECISION_HUB_SETTINGS_PRIVATE_CONFIG", "").strip()
    digest = os.getenv("DECISION_HUB_SETTINGS_PRIVATE_SHA256", "").strip()
    if not name and not digest:
        return None
    if not name or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("decision_settings_private_config_required")
    path = Path(name)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("decision_settings_private_config_path_invalid")
    with path.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("decision_settings_private_config_changed")
    value = PrivateConfig.model_validate_json(raw)
    return LocalOwnerSessionAuthority(
        signing_key=bytes.fromhex(value.signing_key_hex), pairing_sha256=bytes.fromhex(value.pairing_sha256),
        owner_identity=value.owner_identity, domain_pack_ref="crypto_macro.v1", origin=value.origin,
        lifetime_seconds=value.lifetime_seconds, maximum_lifetime_seconds=28_800,
    )


class PrivateConfig(Transport):
    schema_version: Literal["decision-settings-private-launch.v1"]
    owner_identity: str = Field(min_length=1, max_length=128)
    origin: str = Field(min_length=1, max_length=2048)
    signing_key_hex: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    pairing_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    lifetime_seconds: int = Field(default=28_800, ge=1, le=28_800)


def decision_settings_router(
    database: Database, *, authority: LocalOwnerSessionAuthority | None = None,
    source: OKXPerpetualSource | None = None, now: Callable[[], datetime] | None = None,
    research_service=None,
    historical_reader: DevelopmentHistoryReader | None = None,
) -> APIRouter:
    router = APIRouter(prefix=BASE, tags=["decision-settings"])
    clock = now or (lambda: datetime.now(UTC))
    source = source or OKXPerpetualSource(database, RawBodyArchive(Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "raw-captures"))
    paper_service = PerpetualPaperService(database, source, now=clock)
    service = DecisionSettingsService(database, now=clock, binding_reader=DecisionSettingsBindingReader(source, now=clock, paper_account_reader=paper_service.account_reader))

    def available():
        if authority is None:
            raise HTTPException(503, "decision_settings_owner_session_not_configured")
        return authority

    def owner(request: Request, response: Response, mutation=False):
        response.headers["Cache-Control"] = "no-store"
        configured = available()
        try:
            principal = configured.resolve(request.cookies.get(COOKIE, ""), origin=request.headers.get("origin"), mutation=mutation, csrf_token=request.headers.get("x-settings-csrf"))
            if mutation:
                configured.require_owner(principal, owner_identity=configured.owner_identity, domain_pack_ref=configured.domain_pack_ref, operation="register")
            return principal.owner_identity
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None

    def guarded(operation):
        try:
            return operation()
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ValueError as error:
            code = str(error)
            if code == "decision_settings_command_missing":
                raise HTTPException(404, code) from None
            if not code.startswith(("decision_settings_", "decision_execution_profile_", "perp_")):
                code = "decision_settings_original_unavailable"
            raise HTTPException(409, code) from None
        except OSError:
            raise HTTPException(409, "decision_settings_original_unavailable") from None

    from apps.hub_api.perpetual_research_entry_routes import perpetual_research_entry_router
    if research_service is None:
        from packages.runtime_adapters.perpetual_research_entry import configured_perpetual_research_services
        research_service = configured_perpetual_research_services(database, source=source, now=clock).entry
    router.include_router(perpetual_research_entry_router(research_service, owner=owner))
    from apps.hub_api.perpetual_research_schedule_routes import perpetual_research_schedule_router
    from packages.kernel.decision_hub_kernel.application.perpetual_research_schedule import PerpetualResearchScheduleService
    router.include_router(perpetual_research_schedule_router(PerpetualResearchScheduleService(database, research_service, now=clock), owner=owner))
    from apps.hub_api.perpetual_plan_watch_routes import perpetual_plan_watch_router
    from packages.kernel.decision_hub_kernel.application.perpetual_plan_followup import PerpetualPlanFollowupService
    router.include_router(perpetual_plan_watch_router(PerpetualPlanFollowupService(database, research_service, now=clock), owner=owner))

    from apps.hub_api.perpetual_advice_routes import perpetual_advice_router
    from packages.kernel.decision_hub_kernel.application.perpetual_advice_query import PerpetualAdviceQuery
    router.include_router(perpetual_advice_router(PerpetualAdviceQuery(database, now=clock), owner=owner))

    from apps.hub_api.perpetual_saved_report_routes import perpetual_saved_report_router
    from packages.kernel.decision_hub_kernel.application.perpetual_forecast_result_view import PerpetualForecastResultQuery
    from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import PerpetualSavedReportQuery
    router.include_router(perpetual_saved_report_router(
        PerpetualSavedReportQuery(database, now=clock, historical_reader=historical_reader),
        owner=owner,
        result_query=PerpetualForecastResultQuery(database, now=clock),
    ))

    def option(session, instrument_id, reference):
        contract = source.read_contract_in_session(session, reference)
        if f"{contract.venue}:{contract.inst_id}" != instrument_id:
            raise ValueError("decision_settings_instrument_binding_mismatch")
        return InstrumentOption(instrument_id=instrument_id, instrument=reference, contract=contract, execution_profile=paper_execution_profile_reference(instrument_id))

    def options(owner_id):
        items = []
        paper_accounts = []
        with database.session() as session:
            for instrument_id in INSTRUMENTS:
                inst_id = instrument_id.split(":", 1)[1]
                # A saved, authenticated contract is the only selectable item.
                rows = session.scalars(select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.schema_version == "perp-instrument-original.v1",
                    RunRoleOutputRecord.role == f"perp_instrument_contract:{inst_id}",
                ).order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id).limit(16)).all()
                try:
                    candidates = []
                    for row in rows:
                        try:
                            known = as_utc(datetime.fromisoformat(json.loads(row.payload_json)["contract"]["known_at"]))
                        except (KeyError, ValueError, TypeError):
                            known = as_utc(row.created_at)
                        candidates.append((known, 0, DecisionSourceRefV1(ref=row.output_id, hash=row.payload_hash), None))
                    # Compare source knowledge time, never prefer an old Run
                    # merely because it happens to exist. Validate only the
                    # selected newest candidate; corruption cannot fall back.
                    capture = session.scalar(select(RawCaptureRecord).where(
                        RawCaptureRecord.url == public_url(inst_id, "metadata"),
                        RawCaptureRecord.adapter_version == VERSION,
                    ).order_by(RawCaptureRecord.recorded_at.desc(), RawCaptureRecord.capture_id).limit(1))
                    if capture is not None:
                        candidates.append((as_utc(capture.recorded_at), 1, None, capture.capture_id))
                    if candidates:
                        _, _, reference, capture_id = max(candidates, key=lambda item: (item[0], item[1]))
                        if capture_id is not None:
                            reference = source.metadata_contract_reference(capture_id)
                        items.append(option(session, instrument_id, reference))
                except (ValueError, OSError):
                    # Do not silently substitute another contract or source.
                    continue
            heads = session.scalars(select(PerpetualPaperHeadRecord).where(PerpetualPaperHeadRecord.owner_id == owner_id).order_by(PerpetualPaperHeadRecord.account_id).limit(20)).all()
            for head in heads:
                for instrument_id in INSTRUMENTS:
                    profile = paper_execution_profile_reference(instrument_id)
                    partition = DecisionSettingsPartitionV1(account_kind="paper", account_id=head.account_id, instrument_id=instrument_id, profile_ref=profile.ref)
                    try:
                        account, position = paper_service.account_reader(session, owner_id, partition)
                        paper_accounts.append(PaperOption(partition=partition, account_snapshot=account, position_context=position, execution_profile=profile))
                    except (ValueError, OSError):
                        continue
        return SettingsOptions(owner_id=owner_id, declared_account_id=declared_account_id(owner_id, "pc-settings.v1"), availability="available" if items else "unavailable", reason=None if items else "decision_settings_instrument_original_unavailable", instruments=items,
                               paper_available=bool(paper_accounts), paper_reason=None if paper_accounts else "decision_settings_paper_account_unavailable", paper_accounts=paper_accounts)

    @router.post("/owner-session")
    def pair(body: Pair, request: Request, response: Response):
        configured = available()
        session = guarded(lambda: configured.pair(body.pairing_token, origin=request.headers.get("origin", "")))
        response.set_cookie(COOKIE, session.cookie_value, max_age=configured.lifetime_seconds, httponly=True, samesite="strict", path=BASE)
        response.headers["Cache-Control"] = "no-store"
        return {"owner_id": configured.owner_identity, "csrf_token": session.csrf_token, "expires_at": datetime.fromtimestamp(session.expires_at, UTC)}

    @router.get("/options", response_model=None)
    def get_options(request: Request, response: Response):
        return guarded(lambda: options(owner(request, response)))

    @router.post("/instruments/prepare", response_model=None)
    async def prepare(body: PrepareInstrument, request: Request, response: Response):
        owner(request, response, True)
        try:
            # Collection, archive persistence and parsing precede settings' writer.
            capture_id = await source.collect_one(inst_id=body.instrument_id.split(":", 1)[1], kind="metadata")
            def read_prepared():
                reference = source.metadata_contract_reference(capture_id)
                with database.session() as session:
                    return option(session, body.instrument_id, reference)
            return await run_in_threadpool(read_prepared)
        except (ValueError, OSError):
            raise HTTPException(409, "decision_settings_instrument_original_unavailable") from None
        except Exception:
            # Upstream URLs, response payloads and private configuration never leak.
            raise HTTPException(503, "decision_settings_instrument_source_unavailable") from None

    # FastAPI's nullable response TypeAdapter loses generated string alias
    # regex configuration. The service already validates the generated DTO.
    @router.get("/current", response_model=None)
    def current(request: Request, response: Response, account_kind: Literal["user_declared", "paper"], account_id: str, instrument_id: Literal["okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"], profile_ref: str):
        identity = owner(request, response)
        try:
            partition = DecisionSettingsPartitionV1(account_kind=account_kind, account_id=account_id, instrument_id=instrument_id, profile_ref=profile_ref)
        except ValidationError:
            raise HTTPException(422, "decision_settings_partition_invalid") from None
        return guarded(lambda: service.current(identity, partition))

    @router.post("/commands", response_model=DecisionSettingsSaveReceiptV1)
    async def save(request: Request, response: Response):
        identity = owner(request, response, True)
        try:
            # Keep the generated model's Python regex engine when FastAPI's
            # TypeAdapter rebuilds aliases under its default Rust engine.
            body = DecisionSettingsCommandV1.model_validate(await request.json())
        except (ValidationError, ValueError):
            raise HTTPException(422, "decision_settings_command_invalid") from None
        if request.headers.get("idempotency-key", body.request_id) != body.request_id:
            raise HTTPException(409, "decision_settings_request_identity_mismatch")
        return await run_in_threadpool(guarded, lambda: service.save(identity, body))

    @router.get("/commands/{request_id}", response_model=DecisionSettingsSaveReceiptV1)
    def receipt(request_id: str, request: Request, response: Response):
        identity = owner(request, response)
        return guarded(lambda: service.receipt(identity, request_id))

    # Root's companion router shares this exact session/Origin/CSRF owner.
    from apps.hub_api.perpetual_paper_routes import perpetual_paper_account_router
    router.include_router(perpetual_paper_account_router(paper_service, owner=owner))
    from apps.hub_api.perpetual_manual_paper_routes import perpetual_manual_paper_router
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import PerpetualPaperProtocolService
    from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import PerpetualManualPaperService
    from packages.kernel.decision_hub_kernel.application.perpetual_manual_input_guide import PerpetualManualInputGuideQuery
    manual_paper = PerpetualManualPaperService(database, paper_service,
        PerpetualPaperProtocolService(database, source, clock=clock), clock=clock)
    router.include_router(perpetual_manual_paper_router(
        manual_paper, PerpetualManualInputGuideQuery(manual_paper, clock=clock), owner=owner))
    return router
