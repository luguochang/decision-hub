"""Exact perpetual contracts backed by the existing raw-capture archive.

Existing HTTP capture + RawCaptureRecord/archive + per-Run role originals.
Metadata-only settings references reconstruct from the immutable capture;
they need neither a research Run nor a second mutable contract registry.
No new tables, SDK, orders, account calls, source scheduler or mutable defaults.
"""
from __future__ import annotations

import json
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1, InstrumentContractV1,
)
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch, CapturingAsyncClient, RawBodyArchive, capture_scope,
)
from ..persistence.action_quote_repository import _capture
from ..persistence.db import Database, RawCaptureRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.original_preparation import PreparedOriginals, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.okx_perpetual_original import (
    IDENTITIES, VERSION, Kind, PublicOriginal, RuleInputs, assemble_contract, decode_public_original, public_url,
)
from .fact_lineage import FactLineageStore

KINDS: tuple[Kind, ...] = ("metadata", "ticker", "mark", "index", "funding")
METADATA_CONTRACT_PREFIX = "perp_metadata_contract:"
RuleReader = Callable[[Session, str, DecisionSourceRefV1], RuleInputs]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class PreparedContract:
    contract: InstrumentContractV1
    capture_ids: Mapping[Kind, str]
    source_refs: Mapping[Kind, DecisionSourceRefV1]
    rules: RuleInputs
    cutoff: datetime


_ISSUED_CONTRACTS: dict[int, tuple[weakref.ReferenceType, str]] = {}


async def collect_public_original_capture(
    *, inst_id: str, kind: Kind, archive: RawBodyArchive,
    persist: Callable[[dict[str, Any]], None],
    capture_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Use the original public capture protocol, optionally within a research call."""
    url = public_url(inst_id, kind)

    def record(entry: dict[str, Any]) -> None:
        persist(entry)
        if capture_sink is not None:
            capture_sink.append(entry)

    batch = CaptureBatch(
        f"{VERSION}:{inst_id}:{kind}:{uuid4().hex}", VERSION, archive, record,
    )
    with capture_scope(batch):
        async with CapturingAsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(url)
    response.raise_for_status()
    if len(batch.captures) != 1:
        raise ValueError("perp_one_capture_required")
    return cast(dict[str, Any], batch.captures[0])


class OKXPerpetualOriginalReader:
    """Archive/session-only original reader; no Database, network or writer."""

    def __init__(self, archive: RawBodyArchive, *, rule_reader: RuleReader | None = None):
        self.archive, self.rule_reader = archive, rule_reader

    def read_original_in_session(
        self, session: Session, *, capture_id: str, inst_id: str, kind: Kind, cutoff: datetime,
    ) -> PublicOriginal:
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perp_capture_missing")
        item = decode_public_original(kind, inst_id, _capture(row), self.archive.read(row.body_ref))
        if aware_utc(item.capture.recorded_at) > aware_utc(cutoff):
            raise ValueError("perp_original_not_known_at_cutoff")
        return item

    def _metadata_contract_in_session(self, session: Session, capture_id: str) -> InstrumentContractV1:
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perp_capture_missing")
        identity = row.context_id.split(":")
        if (len(identity) != 4 or identity[0] != VERSION
                or identity[1] not in IDENTITIES or identity[2] != "metadata"):
            raise ValueError("perp_metadata_capture_identity_invalid")
        cutoff = as_utc(row.recorded_at)
        original = self.read_original_in_session(
            session, capture_id=capture_id, inst_id=identity[1], kind="metadata", cutoff=cutoff,
        )
        return assemble_contract({"metadata": original}, RuleInputs(), cutoff=cutoff)

    @staticmethod
    def _metadata_contract_hash(capture_id: str, contract: InstrumentContractV1) -> str:
        return canonical_hash({
            "schema_version": "perp-metadata-contract-reference.v1",
            "capture_id": capture_id, "contract": contract.model_dump(mode="json"),
        })

    def _prepare_value(self, session, *, inst_id, capture_ids, rule_ref, cutoff):
        if "metadata" not in capture_ids or not set(capture_ids) <= set(KINDS):
            raise ValueError("perp_metadata_capture_required")
        originals = {kind: self.read_original_in_session(
            session, capture_id=capture_ids[kind], inst_id=inst_id, kind=kind, cutoff=cutoff,
        ) for kind in capture_ids}
        rules = RuleInputs()
        if rule_ref is not None:
            if self.rule_reader is None:
                raise ValueError("perp_real_rule_reader_required_for_bound_rules")
            rules = self.rule_reader(session, inst_id, rule_ref)
            if rules.binding_original != rule_ref:
                raise ValueError("perp_rule_original_substitution")
        return PreparedContract(
            assemble_contract(originals, rules, cutoff=cutoff), dict(capture_ids),
            {kind: item.source_ref for kind, item in originals.items()}, rules, aware_utc(cutoff),
        )

    @staticmethod
    def _body(run_id: str, value: PreparedContract) -> dict:
        return {
            "schema_version": "perp-instrument-original.v1", "run_id": run_id,
            "contract": value.contract.model_dump(mode="json"),
            "capture_ids": dict(value.capture_ids),
            "source_refs": {key: ref.model_dump(mode="json") for key, ref in value.source_refs.items()},
            "rule_binding": value.rules.binding_original.model_dump(mode="json") if value.rules.binding_original else None,
            "effective_original": value.rules.effective_original.model_dump(mode="json") if value.rules.effective_original else None,
            "funding_schedule_original": value.rules.funding_schedule_original.model_dump(mode="json") if value.rules.funding_schedule_original else None,
            "margin_tier_original": value.rules.margin_tier_original.model_dump(mode="json") if value.rules.margin_tier_original else None,
            "cutoff": value.cutoff.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }

    def read_contract_in_session(self, session: Session, reference: DecisionSourceRefV1) -> InstrumentContractV1:
        if reference.ref.startswith(METADATA_CONTRACT_PREFIX):
            capture_id = reference.ref.removeprefix(METADATA_CONTRACT_PREFIX)
            contract = self._metadata_contract_in_session(session, capture_id)
            if self._metadata_contract_hash(capture_id, contract) != reference.hash:
                raise ValueError("perp_metadata_contract_hash_mismatch")
            return contract
        row = session.get(RunRoleOutputRecord, reference.ref, populate_existing=True)
        if row is None or row.schema_version != "perp-instrument-original.v1":
            raise ValueError("perp_contract_original_missing")
        body = json.loads(row.payload_json)
        if (canonical_hash(body) != row.payload_hash or row.payload_hash != reference.hash
                or canonical(body) != row.payload_json or row.run_id != body["run_id"]
                or row.output_id != "perp_contract_" + row.payload_hash):
            raise ValueError("perp_contract_original_hash_mismatch")
        saved = InstrumentContractV1.model_validate(body["contract"])
        if row.role != f"perp_instrument_contract:{saved.inst_id}":
            raise ValueError("perp_contract_original_role_mismatch")
        value = self._prepare_value(
            session, inst_id=saved.inst_id, capture_ids=body["capture_ids"],
            rule_ref=DecisionSourceRefV1.model_validate(body["rule_binding"]) if body["rule_binding"] is not None else None,
            cutoff=datetime.fromisoformat(body["cutoff"]),
        )
        if self._body(row.run_id, value) != body:
            raise ValueError("perp_contract_original_reconstruction_mismatch")
        return value.contract



def read_contract_from_archive_in_session(
    session: Session, reference: DecisionSourceRefV1, archive: RawBodyArchive,
    *, rule_reader: RuleReader | None = None,
) -> InstrumentContractV1:
    return OKXPerpetualOriginalReader(archive, rule_reader=rule_reader).read_contract_in_session(session, reference)


class OKXPerpetualSource(OKXPerpetualOriginalReader):
    def __init__(self, database: Database, archive: RawBodyArchive, *, rule_reader: RuleReader | None = None):
        super().__init__(archive, rule_reader=rule_reader)
        self.database = database
        self.lineage = FactLineageStore(database)

    async def collect_one(self, *, inst_id: str, kind: Kind) -> str:
        # Resolve exact allowlisted endpoint BEFORE any network I/O. A future
        # normal adapter composition calls this under its existing collection
        # budget; this method does not create a background sampling service.
        captured = await collect_public_original_capture(
            inst_id=inst_id, kind=kind, archive=self.archive,
            persist=self.lineage.record_capture,
        )
        # Raw bytes + metadata have committed through the existing capture
        # callback before provider/business parsing. Failure remains a capture.
        return captured["capture_id"]




    def metadata_contract_reference(self, capture_id: str) -> DecisionSourceRefV1:
        """Authenticate an archived metadata response for settings before research.

        This is a versioned source reference, not an execution authorization.
        Missing quote/rules/account values remain unavailable on the contract.
        Every subsequent consumer reconstructs the exact same archived source.
        """
        with self.database.session() as session:
            contract = self._metadata_contract_in_session(session, capture_id)
            return DecisionSourceRefV1(
                ref=METADATA_CONTRACT_PREFIX + capture_id,
                hash=self._metadata_contract_hash(capture_id, contract),
            )


    def prepare_contract(
        self, *, inst_id: str, capture_ids: Mapping[Kind, str], rule_ref: DecisionSourceRefV1 | None = None,
        cutoff: datetime,
    ) -> PreparedOriginals[PreparedContract]:
        # All raw/source authentication and parsing precedes the caller's writer.
        prepared = prepare_originals(self.database, lambda session: self._prepare_value(
            session, inst_id=inst_id, capture_ids=capture_ids, rule_ref=rule_ref, cutoff=cutoff,
        ))
        identity = id(prepared)
        _ISSUED_CONTRACTS[identity] = (
            weakref.ref(prepared, lambda _ref: _ISSUED_CONTRACTS.pop(identity, None)),
            canonical_hash(self._body("", prepared.value)),
        )
        return prepared


    def save_prepared_in_session(
        self, session: Session, *, run_id: str, prepared: PreparedOriginals[PreparedContract],
        writer_guard: Callable[[Session], None],
    ) -> DecisionSourceRefV1:
        # The existing caller owns BEGIN IMMEDIATE and fresh Run/owner/lease/
        # generation fences. No nested writer or independent research authority.
        # A generic readset does not authenticate a caller-replaced value. Keep
        # the actual issued preparation and bind every nested DTO/map to the
        # envelope built by this source reader before allowing any row write.
        prepared._require_issued(session)
        issued = _ISSUED_CONTRACTS.get(id(prepared))
        if issued is None or issued[0]() is not prepared:
            raise ValueError("original_preparation_unissued")
        prepared.verify(session)
        writer_guard(session)
        value = prepared.value
        body = self._body(run_id, value)
        if canonical_hash({**body, "run_id": ""}) != issued[1]:
            raise ValueError("perp_prepared_value_changed")
        role = f"perp_instrument_contract:{body['contract']['inst_id']}"
        digest = canonical_hash(body)
        output_id = "perp_contract_" + digest
        previous = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == role,
        ).execution_options(populate_existing=True))
        if previous is not None:
            if (previous.output_id != output_id or previous.payload_hash != digest
                    or previous.payload_json != canonical(body) or previous.schema_version != body["schema_version"]):
                raise ValueError("perp_frozen_run_instrument_conflict")
            return DecisionSourceRefV1(ref=output_id, hash=digest)
        session.add(RunRoleOutputRecord(
            output_id=output_id, run_id=run_id, role=role, schema_version=body["schema_version"],
            payload_json=canonical(body), payload_hash=digest, created_at=utcnow(),
        ))
        session.flush()
        prepared.verify_after_writes(session)
        return DecisionSourceRefV1(ref=output_id, hash=digest)

