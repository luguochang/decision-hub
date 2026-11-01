"""Signed simulated-account commands; this router exposes no trade operation."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    CreatePerpetualPaperAccountV1,
    PaperAccountCreationReceiptV1,
    PaperAccountListV1,
    PaperAccountSummaryV1,
)
from packages.kernel.decision_hub_kernel.application.perpetual_paper import (
    CreatePaperAccount,
    PerpetualPaperService,
)
from packages.kernel.decision_hub_kernel.ports.action_quote import decode_json, text_at
from packages.kernel.decision_hub_kernel.ports.exact_decimal_text import number
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def _command(raw: object) -> tuple[CreatePerpetualPaperAccountV1, CreatePaperAccount]:
    if not isinstance(raw, dict) or raw.get("simulation_acknowledged") is not True:
        raise HTTPException(422, "perp_paper_explicit_simulation_ack_required")
    try:
        public = CreatePerpetualPaperAccountV1.model_validate(raw)
        # The ledger's canonical amount is a plain, exact decimal string.
        # Lexically different representations of one amount have one command.
        public = CreatePerpetualPaperAccountV1.model_validate({
            **public.model_dump(mode="json"),
            "initial_cash_usdt": number(Decimal(public.initial_cash_usdt)),
        })
        internal = CreatePaperAccount(
            request_id=public.request_id, account_id=public.account_id,
            instrument_refs=tuple(public.instrument_refs), profile=public.profile,
            initial_cash_usdt=Decimal(public.initial_cash_usdt),
            simulation_acknowledged=True, execution_mode=public.execution_mode,
        )
        return public, internal
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(422, "perp_paper_account_command_invalid") from None


def _summary(value: dict) -> PaperAccountSummaryV1:
    fields = (
        "owner_id", "account_id", "profile", "instrument_refs", "state_version", "revision",
        "execution_mode", "execution_enabled", "created_at",
    )
    return PaperAccountSummaryV1.model_validate({
        "schema_version": "perpetual-paper-account-summary.v1",
        **{key: value[key] for key in fields},
        "created_at": text_at(datetime.fromisoformat(value["created_at"])).replace("+00:00", "Z"),
    })


def _receipt(owner: str, command: CreatePerpetualPaperAccountV1, value: dict):
    if (value["owner_id"] != owner or value["account_id"] != command.account_id
            or value["state_version"] != 1
            or value["event"]["kind"] != "simulation_account_created"):
        raise ValueError("perp_paper_creation_command_original_invalid")
    return PaperAccountCreationReceiptV1.model_validate({
        "schema_version": "perpetual-paper-account-create-receipt.v1",
        "owner_id": owner, "request_id": command.request_id,
        "command_hash": canonical_hash(command.model_dump(mode="json")),
        "account": _summary(value).model_dump(mode="json"),
    }).model_dump(mode="json")


def _business(operation: Callable):
    try:
        return operation()
    except PermissionError:
        raise HTTPException(403, "perp_paper_account_owner_rejected") from None
    except (ValueError, OSError) as error:
        code = str(error)
        if not code.startswith(("perp_paper_", "perp_", "decision_execution_profile_")):
            code = "perp_paper_account_original_unavailable"
        raise HTTPException(404 if code == "perp_paper_command_missing" else 409, code) from None


def perpetual_paper_account_router(
    paper_service: PerpetualPaperService,
    *,
    owner: Callable[[Request, Response, bool], str],
) -> APIRouter:
    router = APIRouter(prefix="/paper-accounts", tags=["perpetual-paper-accounts"])

    @router.get("", response_model=None)
    async def list_accounts(
        request: Request, response: Response,
        limit: int = Query(100, ge=1, le=100),
        after_account_id: str | None = Query(None, min_length=1, max_length=128),
    ):
        identity = owner(request, response, False)

        def read():
            accounts = paper_service.list_accounts(identity, limit=limit, after_account_id=after_account_id)
            return PaperAccountListV1.model_validate({
                "schema_version": "perpetual-paper-account-list.v1", "owner_id": identity,
                "accounts": [_summary(item).model_dump(mode="json") for item in accounts],
                "next_after_account_id": accounts[-1]["account_id"] if len(accounts) == limit else None,
            }).model_dump(mode="json")

        return await run_in_threadpool(_business, read)

    async def parse(request: Request):
        try:
            raw = decode_json(await request.body())
        except ValueError:
            raise HTTPException(422, "perp_paper_account_command_invalid") from None
        public, internal = _command(raw)
        key = request.headers.get("idempotency-key")
        if key is not None and key != public.request_id:
            raise HTTPException(409, "perp_paper_request_binding_mismatch")
        return public, internal

    @router.post("", response_model=None)
    async def create(request: Request, response: Response):
        identity = owner(request, response, True)
        public, internal = await parse(request)
        return await run_in_threadpool(
            _business, lambda: _receipt(identity, public, paper_service.create_account(identity, internal)),
        )

    @router.post("/receipts/{request_id}", response_model=None)
    async def receipt(request_id: str, request: Request, response: Response):
        identity = owner(request, response, True)
        public, internal = await parse(request)
        if request_id != public.request_id:
            raise HTTPException(409, "perp_paper_request_binding_mismatch")
        # Reads only the original command. Missing means unconfirmed, never a
        # request to recreate an account or rerun a financial action.
        return await run_in_threadpool(
            _business, lambda: _receipt(identity, public, paper_service.creation_receipt(identity, request_id, internal)),
        )

    return router
