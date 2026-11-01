"""Explicit simulation accounts and authenticated executable-quote preparation.

No model, publisher, permission, automatic creation or execution path is added.
The internal repository is a ledger seam for the later authorized V2 executor.
"""

from __future__ import annotations

import asyncio
import json
import weakref
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from time import monotonic_ns
from typing import Any, overload
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    AccountSnapshotV2,
    DecisionSettingsPartitionV1,
    DecisionSourceRefV1,
    PositionContextV2,
)
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    capture_scope,
)

from ..decision.perpetual_accounting import LinearContractSpec
from ..persistence.action_quote_repository import _capture
from ..persistence.callback_source_lease import CallbackSourceLease
from ..persistence.db import Database, RawCaptureRecord, utcnow
from ..persistence.original_preparation import PreparedOriginals, begin_immediate, prepare_originals
from ..persistence.perpetual_paper_records import PerpetualPaperHeadRecord as Head
from ..persistence.perpetual_paper_repository import (
    PerpetualPaperRepository,
    account_key,
    identity,
    number,
)
from ..ports.action_quote import decode_json, text_at
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_quote import VERSION, PerpetualBook, decode_book, public_url
from ..ports.perpetual_quote_clock import (
    MEASUREMENT_ADAPTER,
    MEASUREMENT_URL,
    TIME_URL,
    reconstruct_book_clock,
)
from .decision_execution_profile import read_execution_profile
from .okx_perpetual_source import OKXPerpetualSource


@dataclass(frozen=True)
class CreatePaperAccount:
    request_id: str
    account_id: str
    instrument_refs: tuple[DecisionSourceRefV1, ...]
    profile: DecisionSourceRefV1
    initial_cash_usdt: Decimal
    simulation_acknowledged: bool
    execution_mode: str


@dataclass(frozen=True)
class PreparedPaperQuote:
    instrument_ref: DecisionSourceRefV1
    spec: LinearContractSpec
    book: PerpetualBook


@dataclass(frozen=True)
class PreparedQuoteBundle[T]:
    value: T
    quote: PreparedPaperQuote | None


@dataclass(frozen=True)
class CollectedPaperQuote:
    capture_id: str
    clock_measurement_id: str


_ISSUED_QUOTES: dict[int, tuple[weakref.ReferenceType, str]] = {}
@dataclass(frozen=True)
class _BundleIssue:
    reference: weakref.ReferenceType[PreparedOriginals[PreparedQuoteBundle[Any]]]
    bundle: PreparedQuoteBundle[Any]
    value: Any
    value_hash: str
    quote: PreparedPaperQuote | None
    quote_hash: str | None


_ISSUED_QUOTE_BUNDLES: dict[int, _BundleIssue] = {}


def _quote_hash(value: PreparedPaperQuote) -> str:
    spec = {
        key: number(item) if isinstance(item, Decimal) else item
        for key, item in asdict(value.spec).items()
    }
    book = {
        key: item.isoformat() if isinstance(item, datetime) else item
        for key, item in asdict(value.book).items()
    }
    return canonical_hash([value.instrument_ref.model_dump(mode="json"), spec, book])


def _bundle_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if type(value) in (str, int, float, bool, type(None)):
        return [type(value).__name__, value]
    if type(value) in (list, tuple):
        return [type(value).__name__, [_bundle_value(item) for item in value]]
    if type(value) is dict and all(type(key) is str for key in value):
        return ["dict", {key: _bundle_value(item) for key, item in value.items()}]
    raise ValueError("perp_paper_quote_bundle_value_unsupported")


def _require_bundle_binding[T](
    session: Session, prepared: PreparedOriginals[PreparedQuoteBundle[T]]
) -> PreparedQuoteBundle[T]:
    prepared._require_issued(session)
    issued = _ISSUED_QUOTE_BUNDLES.get(id(prepared))
    value = prepared.value
    if (issued is None or issued.reference() is not prepared or value is not issued.bundle
            or value.value is not issued.value
            or canonical_hash(_bundle_value(value.value)) != issued.value_hash
            or value.quote is not issued.quote
            or (value.quote is not None and _quote_hash(value.quote) != issued.quote_hash)):
        raise ValueError("perp_paper_quote_preparation_unissued_or_changed")
    return value


def require_prepared_bundle[T](
    session: Session, prepared: PreparedOriginals[PreparedQuoteBundle[T]]
) -> PreparedQuoteBundle[T]:
    value = _require_bundle_binding(session, prepared)
    prepared.verify(session)
    return value


def verify_prepared_bundle_after_writes[T](
    session: Session, prepared: PreparedOriginals[PreparedQuoteBundle[T]]
) -> None:
    _require_bundle_binding(session, prepared)
    prepared.verify_after_writes(session)


@overload
def require_prepared_quote(
    session: Session, prepared: PreparedOriginals[PreparedPaperQuote]
) -> PreparedPaperQuote: ...


@overload
def require_prepared_quote[T](
    session: Session, prepared: PreparedOriginals[PreparedQuoteBundle[T]]
) -> PreparedPaperQuote: ...


def require_prepared_quote(
    session: Session, prepared: PreparedOriginals[Any]
) -> PreparedPaperQuote:
    prepared._require_issued(session)
    issued = _ISSUED_QUOTES.get(id(prepared))
    if issued is not None and issued[0]() is prepared:
        quote = prepared.value
        if type(quote) is not PreparedPaperQuote or _quote_hash(quote) != issued[1]:
            raise ValueError("perp_paper_quote_preparation_unissued_or_changed")
        prepared.verify(session)
    else:
        quote = require_prepared_bundle(session, prepared).quote
        if quote is None:
            raise ValueError("perp_paper_quote_preparation_unissued_or_changed")
    return quote


def _project_entry(value: str | None) -> str | None:
    # Wire display has 18 decimal places; ledger arithmetic retains its 50-digit
    # weighted entry original. Never feed this rounded projection back to fills.
    if value is None:
        return None
    with localcontext() as context:
        context.prec = 80
        return number(Decimal(value).quantize(Decimal("1e-18"), rounding=ROUND_HALF_EVEN))


class PerpetualPaperService:
    def __init__(
        self, database: Database, source: OKXPerpetualSource, *, now: Callable[[], datetime]
    ):
        if source.database is not database:
            raise ValueError("perp_paper_source_database_mismatch")
        self.database, self.source, self._now = database, source, now

    @staticmethod
    def _creation_inputs(owner_id: str, command: CreatePaperAccount):
        key = account_key(owner_id, command.account_id)
        identity(command.request_id)
        if command.simulation_acknowledged is not True:
            raise ValueError("perp_paper_simulation_ack_required")
        amount = number(command.initial_cash_usdt)
        if command.initial_cash_usdt <= 0:
            raise ValueError("perp_paper_initial_cash_positive_required")
        if command.execution_mode not in {"manual_after_visible", "server_auto_paper"}:
            raise ValueError("perp_paper_execution_mode_invalid")
        if not 1 <= len(command.instrument_refs) <= 2:
            raise ValueError("perp_paper_instrument_selection_required")
        # Freeze caller-owned Pydantic instances before preparing originals.
        refs = tuple(
            DecisionSourceRefV1.model_validate(ref.model_dump(mode="json"))
            for ref in command.instrument_refs
        )
        profile_ref = DecisionSourceRefV1.model_validate(command.profile.model_dump(mode="json"))
        inputs = dict(
            account_id=command.account_id,
            initial_cash_usdt=amount,
            instrument_refs=[ref.model_dump(mode="json") for ref in refs],
            profile=profile_ref.model_dump(mode="json"),
            execution_mode=command.execution_mode,
            simulation_acknowledged=True,
        )
        return key, inputs, refs, profile_ref

    def create_account(self, owner_id: str, command: CreatePaperAccount) -> dict:
        """Owner command creates explicitly supplied simulated USDT, never real funds."""
        key, inputs, refs, profile_ref = self._creation_inputs(owner_id, command)
        amount = inputs["initial_cash_usdt"]
        digest = canonical_hash(inputs)
        with self.database.session() as session:
            old = PerpetualPaperRepository(session).replay(owner_id, command.request_id, digest)
            if old is not None:
                return old

        def originals(session):
            instruments = {}
            for ref in refs:
                contract = self.source.read_contract_in_session(session, ref)
                inst = f"{contract.venue}:{contract.inst_id}"
                read_execution_profile(inst, profile_ref)
                if inst in instruments:
                    raise ValueError("perp_paper_duplicate_instrument")
                # Validate units even for a flat account; quote-valued ctVal is unsupported.
                self._spec(contract)
                instruments[inst] = ref.model_dump(mode="json")
            return instruments

        prepared = prepare_originals(self.database, originals)
        now = aware_utc(self._now())
        with self.database.session() as session:
            begin_immediate(session)
            repo = PerpetualPaperRepository(session)
            old = repo.replay(owner_id, command.request_id, digest)
            if old is not None:
                return old
            prepared._require_issued(session)
            prepared.verify(session)
            if session.get(Head, key, populate_existing=True) is not None:
                raise ValueError("perp_paper_account_exists")
            value = repo.append(
                dict(
                    schema_version="perpetual-paper-ledger.internal.v1",
                    account_key=key,
                    owner_id=owner_id,
                    account_id=command.account_id,
                    state_version=1,
                    parent=None,
                    created_at=now.isoformat(),
                    profile=profile_ref.model_dump(mode="json"),
                    instrument_refs=prepared.value,
                    trade_cash_usdt=amount,
                    execution_mode=command.execution_mode,
                    execution_enabled=False,
                    funding_complete=True,
                    positions={
                        inst: dict(signed_contracts="0", entry_price=None, isolated_margin="0")
                        for inst in prepared.value
                    },
                    event=dict(
                        kind="simulation_account_created",
                        command_hash=digest,
                        initial_cash_usdt=amount,
                        simulation_acknowledged=True,
                        funding_cash_usdt=None,
                    ),
                ),
                request_id=command.request_id,
                digest=digest,
                now=now,
            )
            prepared.verify_after_writes(session)
            return value

    def creation_receipt(
        self,
        owner_id: str,
        request_id: str,
        expected_command: CreatePaperAccount,
    ) -> dict:
        """Recover the exact original create result, even after later account changes."""
        if identity(request_id) != expected_command.request_id:
            raise ValueError("perp_paper_request_binding_mismatch")
        _, inputs, _, _ = self._creation_inputs(owner_id, expected_command)
        with self.database.session() as session:
            value = PerpetualPaperRepository(session).replay(
                owner_id,
                request_id,
                canonical_hash(inputs),
            )
            if value is None:
                raise ValueError("perp_paper_command_missing")
            if value["event"]["kind"] != "simulation_account_created":
                raise ValueError("perp_paper_creation_command_original_invalid")
            return value

    def list_accounts(
        self, owner_id: str, *, limit: int = 100, after_account_id: str | None = None
    ) -> tuple[dict, ...]:
        """Bounded authenticated account summaries; no automatic account allocation."""
        account_key(owner_id, "list-validation")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("perp_paper_list_limit_invalid")
        with self.database.session() as session:
            query = select(Head).where(Head.owner_id == owner_id)
            if after_account_id is not None:
                query = query.where(Head.account_id > identity(after_account_id))
            heads = session.scalars(query.order_by(Head.account_id).limit(limit)).all()
            values = []
            for head in heads:
                value = PerpetualPaperRepository(session).current(owner_id, head.account_id)
                for inst, ref in value["instrument_refs"].items():
                    contract = self.source.read_contract_in_session(
                        session,
                        DecisionSourceRefV1.model_validate(ref),
                    )
                    if inst != f"{contract.venue}:{contract.inst_id}":
                        raise ValueError("perp_paper_instrument_binding_mismatch")
                    read_execution_profile(
                        inst, DecisionSourceRefV1.model_validate(value["profile"])
                    )
                fields = (
                    "owner_id",
                    "account_id",
                    "profile",
                    "instrument_refs",
                    "state_version",
                    "revision",
                    "execution_mode",
                    "execution_enabled",
                    "created_at",
                )
                values.append({key: value[key] for key in fields})
            return tuple(values)

    async def collect_quote(self, *, inst_id: str) -> str:
        """One allowlisted public GET through the existing capture/lineage mechanism."""
        url = public_url(inst_id)
        batch = CaptureBatch(
            f"{VERSION}:{inst_id}:{uuid4().hex}",
            VERSION,
            self.source.archive,
            self.source.lineage.record_capture,
        )
        with capture_scope(batch):
            async with CapturingAsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.get(url)
        response.raise_for_status()
        if len(batch.captures) != 1:
            raise ValueError("perp_paper_one_quote_capture_required")
        return batch.captures[0]["capture_id"]

    async def collect_bracketed_quote(self, *, inst_id: str) -> CollectedPaperQuote:
        """One nonreusable time/book/time bracket; at most three public GETs."""
        url = public_url(inst_id)
        batch = CaptureBatch(
            f"{VERSION}:{inst_id}:{uuid4().hex}",
            VERSION,
            self.source.archive,
            self.source.lineage.record_capture,
        )
        start, mono = utcnow(), monotonic_ns()
        measurements = {}

        async def bounded_get(client, endpoint, seconds):
            response = await asyncio.wait_for(
                client.get(endpoint, timeout=seconds, headers={"Cache-Control": "no-cache"}),
                timeout=seconds,
            )
            response.raise_for_status()

        async def measured_time(client, name):
            before, mono = utcnow(), monotonic_ns()
            await bounded_get(client, TIME_URL, 2)
            measurements[name] = dict(
                started_at=text_at(before),
                completed_at=text_at(utcnow()),
                elapsed_monotonic_ns=monotonic_ns() - mono,
            )

        async def bracket(client):
            await measured_time(client, "before")
            await bounded_get(client, url, 8)
            await measured_time(client, "after")

        with capture_scope(batch):
            async with CapturingAsyncClient(timeout=8, follow_redirects=False) as client:
                await asyncio.wait_for(bracket(client), timeout=14)
        if len(batch.captures) != 3:
            raise ValueError("perp_clock_capture_count_invalid")
        measurements["bracket"] = dict(
            started_at=text_at(start),
            completed_at=text_at(utcnow()),
            elapsed_monotonic_ns=monotonic_ns() - mono,
        )
        body = dict(
            schema_version="perpetual-book-clock-measurement.v1",
            kind="internal_clock_measurement",
            captures=[_capture(RawCaptureRecord(**entry)).payload() for entry in batch.captures],
            **measurements,
        )
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        digest, reference = self.source.archive.put(raw)
        measured_at, clock_id = utcnow(), "raw_" + uuid4().hex
        self.source.lineage.record_capture(
            dict(
                capture_id=clock_id,
                context_id=batch.context_id,
                url=MEASUREMENT_URL,
                status_code=None,
                body_hash=digest,
                body_ref=reference,
                byte_count=len(raw),
                media_type="application/json",
                adapter_version=MEASUREMENT_ADAPTER,
                error_code=None,
                started_at=measured_at,
                completed_at=measured_at,
                recorded_at=utcnow(),
            )
        )
        return CollectedPaperQuote(batch.captures[1]["capture_id"], clock_id)

    @staticmethod
    def _spec(contract):
        return LinearContractSpec(
            base=contract.base,
            quote=contract.quote,
            settle=contract.settle,
            ct_val=Decimal(contract.ct_val),
            ct_mult=Decimal(contract.ct_mult),
            ct_val_ccy=contract.ct_val_ccy,
            lot_sz=Decimal(contract.lot_sz),
            min_sz=Decimal(contract.min_sz),
            tick_sz=Decimal(contract.tick_sz),
        )

    def prepare_quote(
        self,
        *,
        instrument_ref: DecisionSourceRefV1,
        capture_id: str,
        cutoff: datetime,
        clock_measurement_id: str | None = None,
    ) -> PreparedOriginals[PreparedPaperQuote]:
        """Read real archived top-book + exact contract; grants no action permission."""
        ref = DecisionSourceRefV1.model_validate(instrument_ref.model_dump(mode="json"))

        def originals(session):
            return self._read_quote_in_session(
                session, instrument_ref=ref, capture_id=capture_id, cutoff=cutoff,
                clock_measurement_id=clock_measurement_id,
            )

        prepared = prepare_originals(self.database, originals)
        ident = id(prepared)
        _ISSUED_QUOTES[ident] = (
            weakref.ref(prepared, lambda _ref: _ISSUED_QUOTES.pop(ident, None)),
            _quote_hash(prepared.value),
        )
        return prepared

    def _read_quote_in_session(
        self, session: Session, *, instrument_ref: DecisionSourceRefV1, capture_id: str,
        cutoff: datetime, clock_measurement_id: str | None = None,
    ) -> PreparedPaperQuote:
        ref = DecisionSourceRefV1.model_validate(instrument_ref.model_dump(mode="json"))
        contract = self.source.read_contract_in_session(session, ref)
        if contract.state != "live":
            raise ValueError("perp_paper_contract_not_live")
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perp_paper_quote_capture_missing")
        capture, raw = _capture(row), self.source.archive.read(row.body_ref)
        clock = None
        if clock_measurement_id is not None:

            def original(identity):
                found = session.get(RawCaptureRecord, identity, populate_existing=True)
                if found is None or found.body_ref is None:
                    raise ValueError("perp_clock_capture_missing")
                return _capture(found), self.source.archive.read(found.body_ref)

            measurement, measured_raw = original(clock_measurement_id)
            measured = decode_json(measured_raw)
            captures = measured.get("captures")
            if type(captures) is not list or len(captures) != 3:
                raise ValueError("perp_clock_measurement_shape")
            before, before_raw = original(captures[0]["capture_id"])
            after, after_raw = original(captures[2]["capture_id"])
            clock = reconstruct_book_clock(
                quote=capture,
                quote_raw=raw,
                before=before,
                before_raw=before_raw,
                after=after,
                after_raw=after_raw,
                measurement=measurement,
                measurement_raw=measured_raw,
            )
        book = decode_book(
            contract.inst_id,
            capture,
            raw,
            cutoff=cutoff,
            clock_receipt_json=clock,
        )
        return PreparedPaperQuote(ref, self._spec(contract), book)

    def prepare_quote_bundle[T](
        self, operation: Callable[
            [Session, Callable[..., PreparedPaperQuote]], tuple[T, PreparedPaperQuote | None]
        ], *, source_lease: CallbackSourceLease | None = None,
    ) -> PreparedOriginals[PreparedQuoteBundle[T]]:
        """Issue only a native member decoded within this one read transaction."""
        if source_lease is not None and (
            type(source_lease) is not CallbackSourceLease
            or source_lease.defer_unselected_recipe_archives is not False
        ):
            raise ValueError("original_preparation_source_changed")
        collected: dict[int, tuple[PreparedPaperQuote, str]] = {}
        value_binding: list[tuple[Any, str]] = []

        def originals(session: Session) -> PreparedQuoteBundle[T]:
            active = True

            def reader(**arguments: Any) -> PreparedPaperQuote:
                if not active:
                    raise ValueError("perp_paper_quote_bundle_reader_closed")
                quote = self._read_quote_in_session(session, **arguments)
                collected[id(quote)] = quote, _quote_hash(quote)
                return quote

            try:
                value, selected = operation(session, reader)
            finally:
                active = False
            if selected is not None:
                native = collected.get(id(selected))
                if (native is None or native[0] is not selected
                        or _quote_hash(selected) != native[1]):
                    raise ValueError("perp_paper_quote_bundle_member_invalid")
            value_binding.append((value, canonical_hash(_bundle_value(value))))
            return PreparedQuoteBundle(value, selected)

        prepared = (
            prepare_originals(self.database, originals) if source_lease is None
            else prepare_originals(self.database, originals, source_lease=source_lease)
        )
        original_value, value_hash = value_binding[0]
        if (prepared.value.value is not original_value
                or canonical_hash(_bundle_value(prepared.value.value)) != value_hash):
            raise ValueError("perp_paper_quote_bundle_value_changed")
        selected = prepared.value.quote
        if selected is not None and _quote_hash(selected) != collected[id(selected)][1]:
            raise ValueError("perp_paper_quote_bundle_member_changed")
        ident = id(prepared)
        _ISSUED_QUOTE_BUNDLES[ident] = _BundleIssue(
            weakref.ref(prepared, lambda _ref: _ISSUED_QUOTE_BUNDLES.pop(ident, None)),
            prepared.value, original_value, value_hash,
            selected, _quote_hash(selected) if selected is not None else None,
        )
        return prepared

    def account_reader(
        self, session: Session, owner: str, partition: DecisionSettingsPartitionV1
    ) -> tuple[AccountSnapshotV2, PositionContextV2]:
        """Settings binder callable: authenticated owner/account head and all originals."""
        partition = DecisionSettingsPartitionV1.model_validate(partition.model_dump(mode="json"))
        if partition.account_kind != "paper":
            raise ValueError("perp_paper_account_kind_invalid")
        state = PerpetualPaperRepository(session).current(owner, partition.account_id)
        return project_account_state_in_session(
            session, owner=owner, partition=partition, state=state, source=self.source
        )

    def execute_action(self, *args, **kwargs):
        raise ValueError("perp_paper_v2_action_authorization_unavailable")


def project_account_state_in_session(session, *, owner, partition, state, source):
    """One unchanged account projection for current or authenticated specified state."""
    partition = DecisionSettingsPartitionV1.model_validate(partition.model_dump(mode="json"))
    if (state["owner_id"], state["account_id"]) != (owner, partition.account_id):
        raise ValueError("perp_paper_revision_scope_mismatch")
    if partition.account_kind != "paper":
        raise ValueError("perp_paper_account_kind_invalid")
    if partition.profile_ref != state["profile"]["ref"]:
        raise ValueError("perp_paper_profile_binding_mismatch")
    if partition.instrument_id not in state["positions"]:
        raise ValueError("perp_paper_instrument_not_registered")
    original = DecisionSourceRefV1.model_validate(state["revision"])
    positions = []
    for inst, stored in state["positions"].items():
        ref = DecisionSourceRefV1.model_validate(state["instrument_refs"][inst])
        contract = source.read_contract_in_session(session, ref)
        if inst != f"{contract.venue}:{contract.inst_id}":
            raise ValueError("perp_paper_instrument_binding_mismatch")
        read_execution_profile(inst, DecisionSourceRefV1.model_validate(state["profile"]))
        signed = Decimal(stored["signed_contracts"])
        positions.append(
            PositionContextV2.model_validate(
                dict(
                    schema_version="position-context.v2",
                    instrument_id=inst,
                    instrument_ref=ref,
                    state="long" if signed > 0 else "short" if signed < 0 else "flat",
                    signed_contracts=stored["signed_contracts"],
                    entry_price=_project_entry(stored["entry_price"]),
                    isolated_margin=stored["isolated_margin"],
                    margin_mode="isolated",
                    position_mode="net",
                    hedge_leg=None,
                    leverage="1",
                    liquidation_estimate=None,
                    source="paper_policy",
                    ref=original.ref,
                    hash=original.hash,
                    account_ref=original,
                )
            )
        )
    # No funding settlement coverage or mark path is manufactured. A held or
    # previously held account has incomplete equity until those readers exist.
    complete = state["funding_complete"] and all(p.state == "flat" for p in positions)
    cash = state["trade_cash_usdt"] if complete else None
    account = AccountSnapshotV2.model_validate(
        dict(
            schema_version="account-snapshot.v2",
            owner_id=owner,
            account_kind="paper",
            account_id=partition.account_id,
            source="paper_policy",
            as_of=datetime.fromisoformat(state["created_at"]),
            account_mode="single_currency",
            positions=positions,
            collateral=[dict(ccy="USDT", amount=cash, usdt_value=cash, valuation_ref=original)],
            valuation_ccy="USDT",
            equity=cash,
            available_margin=cash,
            fee_tier=None,
            limits=dict(
                max_gross_notional=None,
                max_reserved_margin=None,
                max_fee_buffer=None,
                max_stress_loss=None,
            ),
            original=original,
        )
    )
    return account, next(p for p in positions if p.instrument_id == partition.instrument_id)


def read_account_revision_in_session(session, *, owner, partition, revision_ref, source):
    """Immutable original read: never substitutes or requires the current head."""
    ref = DecisionSourceRefV1.model_validate(revision_ref)
    state = PerpetualPaperRepository(session).revision(ref.ref)
    if state["revision"] != ref.model_dump(mode="json"):
        raise ValueError("perp_paper_revision_original_invalid")
    return project_account_state_in_session(
        session, owner=owner, partition=partition, state=state, source=source
    )
