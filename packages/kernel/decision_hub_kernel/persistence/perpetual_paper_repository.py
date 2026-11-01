"""Same-session ledger mutation primitives, never action/execution authorization.

The future V2 action executor must enforce reviewed revision, eligibility clocks,
first qualifying quote, scope opt-in, risk, and protections before this primitive.
No route or service executes a paper action through this module today.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal as D
from decimal import localcontext

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..decision.perpetual_accounting import (
    execution_price_usdt,
    floor_contracts,
    linear_base_quantity,
    linear_gross_pnl_usdt,
    paper_fill_contracts,
    trade_fee_usdt,
)
from ..ports.exact_decimal_text import number as number
from ..ports.market_path import aware_utc, canonical_hash
from .db import ProductCommandRecord, as_utc
from .perpetual_paper_records import (
    PerpetualPaperCapacityRecord as Capacity,
)
from .perpetual_paper_records import (
    PerpetualPaperHeadRecord as Head,
)
from .perpetual_paper_records import (
    PerpetualPaperRevisionRecord as Revision,
)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def identity(value: str) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise ValueError("perp_paper_identity_invalid")
    return value


def account_key(owner: str, account: str) -> str:
    if identity(owner).startswith("system:"):
        raise ValueError("perp_paper_owner_invalid")
    return "perppaper_" + canonical_hash([owner, identity(account)])


@contextmanager
def _arithmetic():
    with localcontext() as context:
        context.prec = 50
        yield


class PerpetualPaperRepository:
    def __init__(self, session: Session):
        self.session = session

    def _writer(self):
        native = self.session.connection().connection.driver_connection
        if not native.in_transaction:
            raise ValueError("perp_paper_explicit_writer_required")

    def revision(self, revision_id: str) -> dict:
        first = None
        child = None
        seen = set()
        while True:
            if revision_id in seen:
                raise ValueError("perp_paper_revision_cycle")
            seen.add(revision_id)
            row = self.session.get(Revision, revision_id, populate_existing=True)
            if row is None:
                raise ValueError("perp_paper_revision_missing")
            body = json.loads(row.payload_json)
            digest = canonical_hash(body)
            if (
                canonical(body) != row.payload_json
                or digest != row.payload_hash
                or row.revision_id != "perppaperrev_" + digest
                or row.account_key != account_key(body["owner_id"], body["account_id"])
                or row.account_key != body["account_key"]
                or row.state_version != body["state_version"]
                or (body["state_version"] == 1) != (body["parent"] is None)
            ):
                raise ValueError("perp_paper_revision_original_invalid")
            if child is not None and (
                child["parent"] != {"ref": row.revision_id, "hash": digest}
                or child["state_version"] != body["state_version"] + 1
                or child["account_key"] != body["account_key"]
            ):
                raise ValueError("perp_paper_parent_original_invalid")
            value = {**body, "revision": {"ref": row.revision_id, "hash": digest}}
            first = first or value
            if body["parent"] is None:
                return first
            revision_id, child = body["parent"]["ref"], body

    def current(self, owner: str, account: str) -> dict:
        key = account_key(owner, account)
        # The head is a mutable CAS index. Its exact scalar SELECT is in the
        # prepared readset; immutable revision rows carry the original seal.
        head = self.session.execute(
            select(*Head.__table__.columns).where(Head.account_key == key)
        ).one_or_none()
        if head is None:
            raise ValueError("perp_paper_account_missing")
        value = self.revision(head.current_revision_id)
        if (head.owner_id, head.account_id, head.state_version) != (
            owner,
            account,
            value["state_version"],
        ) or (value["account_key"], value["owner_id"], value["account_id"]) != (
            key,
            owner,
            account,
        ):
            raise ValueError("perp_paper_head_original_invalid")
        return value

    @staticmethod
    def request_key(owner: str, request: str) -> str:
        return "perppapercmd_" + canonical_hash([identity(owner), identity(request)])

    def replay(self, owner: str, request: str, digest: str):
        row = self.session.get(
            ProductCommandRecord, self.request_key(owner, request), populate_existing=True
        )
        if row is None:
            return None
        if row.payload_hash != digest:
            raise ValueError("perp_paper_request_conflict")
        value = json.loads(row.result_json)
        original = self.revision(row.target_id)
        if (
            value != original
            or canonical(value) != row.result_json
            or row.owner_id != owner
            or value["owner_id"] != owner
            or row.kind != "perpetual_paper"
            or value["event"]["command_hash"] != digest
            or as_utc(row.created_at).isoformat() != value["created_at"]
        ):
            raise ValueError("perp_paper_command_original_invalid")
        return value

    def append(self, body: dict, *, request_id: str, digest: str, now: datetime) -> dict:
        self._writer()
        digest_body = canonical_hash(body)
        revision_id = "perppaperrev_" + digest_body
        self.session.add(
            Revision(
                revision_id=revision_id,
                account_key=body["account_key"],
                state_version=body["state_version"],
                payload_json=canonical(body),
                payload_hash=digest_body,
            )
        )
        self.session.flush()
        if body["parent"] is None:
            self.session.add(
                Head(
                    account_key=body["account_key"],
                    owner_id=body["owner_id"],
                    account_id=body["account_id"],
                    current_revision_id=revision_id,
                    state_version=1,
                )
            )
        else:
            changed = self.session.execute(
                update(Head)
                .where(
                    Head.account_key == body["account_key"],
                    Head.state_version == body["state_version"] - 1,
                    Head.current_revision_id == body["parent"]["ref"],
                )
                .values(current_revision_id=revision_id, state_version=body["state_version"]),
                execution_options={"synchronize_session": False},
            ).rowcount
            if changed != 1:
                raise ValueError("perp_paper_state_conflict")
        value = {**body, "revision": {"ref": revision_id, "hash": digest_body}}
        self.session.add(
            ProductCommandRecord(
                request_id=self.request_key(body["owner_id"], request_id),
                owner_id=body["owner_id"],
                target_id=revision_id,
                kind="perpetual_paper",
                payload_hash=digest,
                result_json=canonical(value),
                created_at=now,
            )
        )
        self.session.flush()
        return self.replay(body["owner_id"], request_id, digest)

    @_arithmetic()
    def apply_fill(
        self,
        *,
        prepared,
        owner_id: str,
        account_id: str,
        expected_version: int,
        request_id: str,
        phase: str,
        order_side: str,
        requested_contracts: D,
        fee_rate: D,
        slippage_bps: D,
        cost_assumption_ref: str,
        now: datetime,
    ) -> dict:
        from ..application.perpetual_paper import require_prepared_quote

        self._writer()
        quote = require_prepared_quote(self.session, prepared)
        now = aware_utc(now)
        if (
            quote.book.observed_local_upper > now
            or now - quote.book.observed_local_lower > timedelta(seconds=5)
        ):
            raise ValueError("perp_quote_stale_or_future")
        inputs = dict(
            owner_id=owner_id,
            account_id=account_id,
            expected_version=expected_version,
            request_id=identity(request_id),
            phase=phase,
            order_side=order_side,
            requested_contracts=number(requested_contracts),
            fee_rate=number(fee_rate),
            slippage_bps=number(slippage_bps),
            cost_assumption_ref=identity(cost_assumption_ref),
            quote=quote.book.capture_hash,
            instrument_ref=quote.instrument_ref.model_dump(mode="json"),
            clock_receipt=(
                json.loads(quote.book.clock_receipt_json)
                if quote.book.clock_receipt_json is not None
                else None
            ),
            observed_local_lower=quote.book.observed_local_lower.isoformat(),
            observed_local_upper=quote.book.observed_local_upper.isoformat(),
        )
        digest = canonical_hash(inputs)
        old = self.replay(owner_id, request_id, digest)
        if old is not None:
            return old
        current = self.current(owner_id, account_id)
        if current["state_version"] != expected_version:
            raise ValueError("perp_paper_state_conflict")
        inst = quote.book.instrument_id
        if inst not in current["positions"] or current["instrument_refs"][
            inst
        ] != quote.instrument_ref.model_dump(mode="json"):
            raise ValueError("perp_paper_instrument_binding_mismatch")
        if phase not in {"entry", "reduce", "exit"} or order_side not in {"buy", "sell"}:
            raise ValueError("perp_paper_fill_phase_or_side_invalid")
        if not D("0") <= fee_rate < D("1") or requested_contracts <= 0:
            raise ValueError("perp_paper_fill_amount_invalid")
        if floor_contracts(quote.spec, requested_contracts) != requested_contracts:
            raise ValueError("perp_paper_requested_contract_step_invalid")
        position = current["positions"][inst]
        held = D(position["signed_contracts"])
        direction = D("1") if order_side == "buy" else D("-1")
        if phase == "entry" and held * direction < 0:
            raise ValueError("perp_paper_entry_cross_zero_forbidden")
        if phase != "entry" and (held * direction >= 0 or requested_contracts > abs(held)):
            raise ValueError("perp_paper_close_cross_zero_forbidden")
        capkey = (current["account_key"], quote.book.capacity_key, order_side)
        capacity = self.session.get(Capacity, capkey, populate_existing=True)
        used = D(capacity.consumed_contracts) if capacity else D("0")
        # Authenticate mutable capacity against immutable fills in this account chain.
        proved = D("0")
        cursor = current
        while True:
            event = cursor["event"]
            if (
                event.get("quote_capacity_key") == quote.book.capacity_key
                and event.get("order_side") == order_side
            ):
                proved += D(event["filled_contracts"])
            if cursor["parent"] is None:
                break
            cursor = self.revision(cursor["parent"]["ref"])
        if used != proved:
            raise ValueError("perp_paper_quote_capacity_original_invalid")
        size = quote.book.ask_contracts if order_side == "buy" else quote.book.bid_contracts
        filled = paper_fill_contracts(
            quote.spec,
            requested_contracts=requested_contracts,
            opposing_size_contracts=D(size),
            already_consumed_contracts=used,
        )
        if filled == 0:
            raise ValueError("perp_paper_quote_capacity_exhausted")
        price = execution_price_usdt(
            quote.spec,
            order_side=order_side,
            bid=D(quote.book.bid),
            ask=D(quote.book.ask),
            slippage_bps=slippage_bps,
        )
        fee = trade_fee_usdt(quote.spec, contracts=filled, fill_price=price, fee_rate=fee_rate)
        base = linear_base_quantity(quote.spec, filled)
        margin = D(position["isolated_margin"])
        reserved = sum((D(p["isolated_margin"]) for p in current["positions"].values()), D("0"))
        cash = D(current["trade_cash_usdt"])
        gross = D("0")
        if phase == "entry":
            new_margin = base * price
            if cash - reserved < new_margin + fee:
                raise ValueError("perp_paper_insufficient_margin")
            entry = (abs(held) * D(position["entry_price"] or "0") + filled * price) / (
                abs(held) + filled
            )
            margin += new_margin
        else:
            entry = D(position["entry_price"])
            gross = linear_gross_pnl_usdt(
                quote.spec,
                side="long" if held > 0 else "short",
                contracts=filled,
                entry_fill_price=entry,
                exit_fill_price=price,
            )
            margin *= (abs(held) - filled) / abs(held)
        signed = held + direction * filled
        positions = {key: dict(value) for key, value in current["positions"].items()}
        positions[inst] = dict(
            signed_contracts=number(signed),
            entry_price=number(entry) if signed else None,
            isolated_margin=number(margin),
        )
        body = {key: value for key, value in current.items() if key != "revision"}
        body.update(
            parent=current["revision"],
            state_version=current["state_version"] + 1,
            created_at=now.isoformat(),
            positions=positions,
            trade_cash_usdt=number(cash + gross - fee),
            funding_complete=False,
            event={
                **inputs,
                "kind": "paper_fill",
                "command_hash": digest,
                "filled_contracts": number(filled),
                "unfilled_contracts": number(requested_contracts - filled),
                "base_quantity": number(base),
                "fill_price": number(price),
                "trade_fee_usdt": number(fee),
                "gross_pnl_usdt": number(gross),
                "funding_cash_usdt": None,
                "funding_status": "settlement_coverage_unavailable",
                "cost_source": "simulation_assumption",
                "quote_capacity_key": quote.book.capacity_key,
                "mark_risk_price": None,
            },
        )
        if capacity is None:
            self.session.add(
                Capacity(
                    account_key=capkey[0],
                    quote_key=capkey[1],
                    order_side=capkey[2],
                    consumed_contracts=number(used + filled),
                    state_version=1,
                )
            )
        else:
            changed = self.session.execute(
                update(Capacity)
                .where(
                    Capacity.account_key == capkey[0],
                    Capacity.quote_key == capkey[1],
                    Capacity.order_side == capkey[2],
                    Capacity.state_version == capacity.state_version,
                    Capacity.consumed_contracts == capacity.consumed_contracts,
                )
                .values(
                    consumed_contracts=number(used + filled),
                    state_version=capacity.state_version + 1,
                ),
                execution_options={"synchronize_session": False},
            ).rowcount
            if changed != 1:
                raise ValueError("perp_paper_quote_capacity_conflict")
        value = self.append(body, request_id=request_id, digest=digest, now=now)
        prepared.verify_after_writes(self.session)
        return value

    def apply_zero_settlement_coverage(
        self,
        *,
        owner_id: str,
        account_id: str,
        expected_version: int,
        initial_revision: dict,
        episode_ref: dict,
        coverage_ref: dict,
        now: datetime,
    ) -> dict:
        """Append a zero-cash funding revision after a complete source interval proof."""
        self._writer()
        inputs = dict(
            owner_id=identity(owner_id),
            account_id=identity(account_id),
            expected_version=expected_version,
            initial_revision=initial_revision,
            episode_ref=episode_ref,
            coverage_ref=coverage_ref,
            funding_cash_usdt="0",
        )
        digest = canonical_hash(inputs)
        request_id = "funding_" + digest
        old = self.replay(owner_id, request_id, digest)
        if old is not None:
            return old
        current = self.current(owner_id, account_id)
        initial = self.revision(initial_revision["ref"])
        if (
            current["state_version"] != expected_version
            or current["revision"] != episode_ref["ledger_revision"]
            or initial["revision"] != initial_revision
            or not initial["funding_complete"]
            or current["funding_complete"]
            or any(D(item["signed_contracts"]) for item in current["positions"].values())
        ):
            raise ValueError("perp_paper_funding_reconciliation_state_invalid")
        body = {key: value for key, value in current.items() if key != "revision"}
        body.update(
            parent=current["revision"],
            state_version=current["state_version"] + 1,
            created_at=aware_utc(now).isoformat(),
            funding_complete=True,
            event={
                **inputs,
                "kind": "paper_funding_reconciled",
                "command_hash": digest,
                "funding_status": "complete_zero_settlements",
                "cost_source": "okx_public_funding_history_complete_interval",
            },
        )
        return self.append(body, request_id=request_id, digest=digest, now=aware_utc(now))
