"""First-use current admission and immutable authorization for subsequent Run reads."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence

from ..persistence.db import RunRecord, StudyRecord, as_utc
from ..persistence.run_spot_admission import RunSpotAdmissionRecord
from ..persistence.spot_scope_admission import SpotResearchAdmissionRecord
from ..ports.market_path import (
    FrozenContract,
    MarketPathScope,
    aware_utc,
    canonical_hash,
    spot_scope,
)
from .market_scope import require_research_scope
from .research_quality_projection import QualityProjectionContextV1
from .spot_scope_admission import EffectiveSpotRecipe, SpotHostReadiness


class FrozenSpotPathProof(FrozenContract):
    scope_id: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    known_at: AwareDatetime
    semantic_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture_hashes: tuple[str, ...]


class SpotAdmissionProof(FrozenContract):
    schema_version: Literal["spot-trial-admission.v1"]
    purpose: Literal["controlled_trial"]
    product_release_qualified: Literal[False]
    effective: EffectiveSpotRecipe
    host: SpotHostReadiness
    path: FrozenSpotPathProof
    created_at: AwareDatetime
    expires_at: AwareDatetime


@dataclass(frozen=True)
class VerifiedSpotAdmission:
    """Only the server current-admission reader supplies this verified original."""

    admission_id: str
    proof_hash: str
    proof: SpotAdmissionProof
    assessed_at: datetime


class FrozenRunSpotAdmission(FrozenContract):
    schema_version: Literal["research-run-spot-admission.v1"] = "research-run-spot-admission.v1"
    run_id: str
    event_id: str
    study_id: str
    market: Literal["crypto"] = "crypto"
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    horizon_scope: str
    identity_basis: str
    scope: MarketPathScope
    admission_id: str
    admission_proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_binding_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective: EffectiveSpotRecipe
    path: FrozenSpotPathProof
    admitted_at: AwareDatetime

    def matches(self, study: StudyRecord) -> bool:
        return (
            self.study_id == study.study_id
            and self.market == study.market
            and self.instrument == study.instrument
            and self.identity_basis == study.identity_basis != "legacy_unknown"
            and self.horizon_scope == study.horizon_scope
            and self.scope == spot_scope(self.instrument)
        )


SpotAdmissionResolver = Callable[[Session, StudyRecord, datetime], VerifiedSpotAdmission]


def read_spot_admission_original_in_session(
    session: Session, admission_id: str, *, admitted_at: datetime
) -> tuple[SpotResearchAdmissionRecord, SpotAdmissionProof]:
    row = session.get(SpotResearchAdmissionRecord, admission_id, populate_existing=True)
    if row is None:
        raise ValueError("run_spot_admission_source_missing")
    if hashlib.sha256(row.proof_json.encode()).hexdigest() != row.proof_hash:
        raise ValueError("run_spot_admission_source_hash_mismatch")
    proof = SpotAdmissionProof.model_validate_json(row.proof_json)
    at = aware_utc(admitted_at)
    if (
        row.purpose != proof.purpose
        or row.research_scope_id != proof.effective.research_scope_id
        or row.instrument != proof.effective.instrument
        or row.path_scope_id != proof.path.scope_id
        or proof.path.scope_id != spot_scope(proof.effective.instrument).scope_id
        or row.binding_hash != canonical_hash(proof.effective.model_dump(mode="json"))
        or as_utc(row.created_at) != proof.created_at
        or as_utc(row.expires_at) != proof.expires_at
        or not proof.created_at <= at < proof.expires_at
        or proof.path.known_at > proof.created_at
        or not 0 <= (proof.created_at - proof.path.end_at).total_seconds() < 180
        or (proof.path.end_at - proof.path.start_at).total_seconds() != 86400
        or not proof.host.ready
        or not 0 <= (proof.created_at - proof.host.checked_at).total_seconds() < 180
        or proof.host.installed_pointer_hash != proof.effective.installed_pointer.sha256
        or proof.host.plugin_build_hash != proof.effective.plugin_build_hash
    ):
        raise ValueError("run_spot_admission_source_identity_invalid")
    return row, proof


def _writer(session: Session) -> None:
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ValueError("run_spot_admission_writer_required")


def freeze_run_spot_admission_in_session(
    session: Session, *, run: RunRecord, study: StudyRecord, current: VerifiedSpotAdmission
) -> FrozenRunSpotAdmission:
    _writer(session)
    at = as_utc(run.created_at)
    if current.assessed_at != at:
        raise ValueError("run_spot_admission_current_clock_mismatch")
    row, proof = read_spot_admission_original_in_session(
        session, current.admission_id, admitted_at=at
    )
    if proof != current.proof or row.proof_hash != current.proof_hash:
        raise ValueError("run_spot_admission_current_original_mismatch")
    if (study.market, study.instrument) != ("crypto", proof.effective.instrument):
        raise ValueError("run_spot_admission_study_mismatch")
    value = FrozenRunSpotAdmission(
        run_id=run.run_id,
        event_id=run.event_id,
        study_id=study.study_id,
        instrument=proof.effective.instrument,
        horizon_scope=study.horizon_scope,
        identity_basis=study.identity_basis,
        scope=spot_scope(proof.effective.instrument),
        admission_id=row.admission_id,
        admission_proof_hash=row.proof_hash,
        effective_binding_hash=row.binding_hash,
        effective=proof.effective,
        path=proof.path,
        admitted_at=at,
    )
    require_research_scope(study, frozen_spot_admission=value)
    body = json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    previous = session.get(RunSpotAdmissionRecord, run.run_id)
    if previous is not None:
        if previous.payload_json != body:
            raise ValueError("run_spot_admission_original_conflict")
        original = read_run_spot_admission_in_session(session, run.run_id)
        assert original is not None
        return original
    session.add(
        RunSpotAdmissionRecord(
            run_id=run.run_id,
            event_id=run.event_id,
            study_id=study.study_id,
            admission_id=row.admission_id,
            scope_id=value.scope.scope_id,
            proof_hash=row.proof_hash,
            payload_json=body,
            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
            created_at=at,
        )
    )
    session.flush()
    return value


def read_run_spot_admission_in_session(
    session: Session, run_id: str
) -> FrozenRunSpotAdmission | None:
    row = session.get(RunSpotAdmissionRecord, run_id, populate_existing=True)
    if row is None:
        return None
    if hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash:
        raise ValueError("run_spot_admission_payload_hash_mismatch")
    value = FrozenRunSpotAdmission.model_validate_json(row.payload_json)
    run = session.get(RunRecord, run_id, populate_existing=True)
    study = session.get(StudyRecord, value.study_id, populate_existing=True)
    original, proof = read_spot_admission_original_in_session(
        session, value.admission_id, admitted_at=value.admitted_at
    )
    if (
        run is None
        or study is None
        or run.strategy_version != "research.v1"
        or run.run_id != value.run_id
        or run.event_id != value.event_id
        or run.study_id != value.study_id
        or not value.matches(study)
        or as_utc(run.created_at) != value.admitted_at
        or as_utc(row.created_at) != value.admitted_at
        or row.event_id != value.event_id
        or row.study_id != value.study_id
        or row.admission_id != value.admission_id
        or row.scope_id != value.scope.scope_id
        or row.proof_hash != value.admission_proof_hash
        or original.proof_hash != row.proof_hash
        or original.binding_hash != value.effective_binding_hash
        or proof.effective != value.effective
        or proof.path != value.path
    ):
        raise ValueError("run_spot_admission_identity_changed")
    return value


def require_run_research_scope_in_session(
    session: Session, run: RunRecord, *, domain_pack_ref: str | None = None
):
    study = session.get(StudyRecord, run.study_id, populate_existing=True) if run.study_id else None
    if study is None:
        raise ValueError("research_study_scope_missing")
    from .perpetual_research_binding import read_perpetual_admission_identity_in_session
    perpetual = read_perpetual_admission_identity_in_session(session, run.run_id)
    if perpetual is not None:
        if domain_pack_ref is not None and domain_pack_ref != "crypto_macro.v1":
            raise ValueError("perpetual_research_pack_scope_invalid")
        # This explicit server admission grants one 4h research trial; it does
        # not enable the old market catalog or grant outcome/financial release.
        return perpetual[0]["context"]
    frozen = read_run_spot_admission_in_session(session, run.run_id)
    return require_research_scope(
        study, domain_pack_ref=domain_pack_ref, frozen_spot_admission=frozen
    )


def spot_run_context_inputs_in_session(
    session: Session, run_id: str
) -> tuple[ResearchInputEvidence, ResearchInputEvidence]:
    """Readable pointers to verified originals, never market facts or qualification."""
    value = read_run_spot_admission_in_session(session, run_id)
    row = session.get(RunSpotAdmissionRecord, run_id, populate_existing=True)
    if value is None or row is None:
        raise ValueError("run_spot_admission_missing")
    admission_ref = f"research-run-spot-admission:{run_id}:{row.payload_hash}"
    path_ref = f"market-path-manifest:{value.scope.scope_id}:{value.path.manifest_hash}"
    admission_excerpt = json.dumps(
        {
            "original_ref": admission_ref,
            "admission_id": value.admission_id,
            "admission_proof_hash": value.admission_proof_hash,
            "scope_id": value.scope.scope_id,
            "instrument": value.instrument,
            "admitted_at": value.admitted_at.isoformat(),
            "effective_binding_hash": value.effective_binding_hash,
            "purpose": "controlled_trial",
            "product_release_qualified": False,
            "path_risk_model_qualified": False,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    path_excerpt = json.dumps(
        {
            "original_ref": path_ref,
            "scope": value.scope.model_dump(mode="json"),
            "start_at": value.path.start_at.isoformat(),
            "end_at": value.path.end_at.isoformat(),
            "known_at": value.path.known_at.isoformat(),
            "semantic_hash": value.path.semantic_hash,
            "manifest_hash": value.path.manifest_hash,
            # The authenticated admission retains every capture hash. Keep this
            # readable pointer bounded independently of the number of captures.
            "capture_count": len(value.path.capture_hashes),
            "capture_hashes_hash": canonical_hash(list(value.path.capture_hashes)),
            "qualification": "unknown",
            "price_type": "trade",
            "limitation": "closed 1m OHLC; intrabar order unknown; not mark-price liquidation",
        },
        sort_keys=True,
        ensure_ascii=False,
    )

    def input_record(ref: str, excerpt: str) -> ResearchInputEvidence:
        return ResearchInputEvidence(
            evidence_id=ref,
            source_id=ref,
            kind="document",
            authority="unverified",
            source_url=None,
            published_at=None,
            observed_at=value.path.end_at,
            received_at=value.admitted_at,
            content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
            excerpt=excerpt,
        )

    return input_record(admission_ref, admission_excerpt), input_record(path_ref, path_excerpt)


def quality_context_for_spot_run_in_session(
    session: Session, run_id: str
) -> QualityProjectionContextV1:
    value = read_run_spot_admission_in_session(session, run_id)
    if value is None:
        raise ValueError("run_spot_admission_missing")
    inputs = spot_run_context_inputs_in_session(session, run_id)
    return QualityProjectionContextV1(
        scope_id=value.scope.scope_id,
        additional_required_context_refs=tuple(item.evidence_id for item in inputs),
    )
