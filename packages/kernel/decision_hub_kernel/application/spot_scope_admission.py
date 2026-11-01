"""Prepare first spot trials using verified data and effective installed identities.

Server composition supplies the effective recipe and an implementation-backed route
verifier. Declarative route JSON, readiness alone, or an old swap Run cannot grant
admission. This module does not enable a scope globally or qualify product release.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..decision.path_risk import compute_path_risk
from ..persistence.db import Database, StudyRecord, as_utc, utcnow
from ..persistence.spot_scope_admission import SpotResearchAdmissionRecord
from ..ports.market_path import FrozenContract, aware_utc, canonical_hash, spot_scope
from .market_path_store import MarketPathStore

if TYPE_CHECKING:
    from .research_run_scope import VerifiedSpotAdmission


class SpotAdmissionError(ValueError):
    def __init__(self, code: str):
        self.error_code = code
        super().__init__(code)


_SOURCE_WAIT_ISSUER = object()


class SpotSourceFreshnessWait(SpotAdmissionError):
    """A completed authenticated source read, never permission to create a Run."""

    def __init__(
        self, *, instrument: str, scope_id: str, binding_hash: str,
        assessed_at: datetime, latest_close_at: datetime, latest_known_at: datetime,
        _issuer: object,
    ):
        super().__init__("spot_current_original_path_not_fresh")
        self.instrument, self.scope_id, self.binding_hash = instrument, scope_id, binding_hash
        self.assessed_at = assessed_at
        self.latest_close_at, self.latest_known_at = latest_close_at, latest_known_at
        self._issuer = _issuer

    def matches_condition(self, instrument: str, now: datetime) -> bool:
        return (
            self._issuer is _SOURCE_WAIT_ISSUER
            and self.instrument == instrument
            and self.scope_id == spot_scope(instrument).scope_id
            and len(self.binding_hash) == 64
            and self.latest_close_at <= self.latest_known_at <= self.assessed_at <= now
            and (self.assessed_at - self.latest_close_at).total_seconds() >= 180
        )


class HashedResource(FrozenContract):
    ref: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SpotCapabilityRoute(FrozenContract):
    capability_id: str = Field(min_length=1)
    asset_mode: Literal["global_macro", "instrument"]
    instrument: Literal["BTC-USDT", "ETH-USDT"] | None = None
    manifest: HashedResource
    route: HashedResource

    @model_validator(mode="after")
    def exact_asset_route(self):
        if (self.asset_mode == "instrument") != (self.instrument is not None):
            raise ValueError("spot_asset_route_identity_invalid")
        return self


class EffectiveSpotRecipe(FrozenContract):
    """Trusted server projection of the actual installed closure, never a client DTO."""

    research_scope_id: Literal["btc-spot-macro.v1", "eth-spot-macro.v1"]
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    domain_pack_ref: Literal["crypto_macro.v1"] = "crypto_macro.v1"
    recipe: HashedResource
    pack: HashedResource
    installed_pointer: HashedResource
    plugin_build_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_capability_ids: tuple[str, ...] = Field(min_length=1)
    routes: tuple[SpotCapabilityRoute, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope_routes(self):
        if self.research_scope_id != f"{self.instrument.split('-')[0].lower()}-spot-macro.v1":
            raise ValueError("spot_recipe_scope_mismatch")
        ids = tuple(route.capability_id for route in self.routes)
        if (
            len(set(ids)) != len(ids)
            or len(set(self.required_capability_ids)) != len(self.required_capability_ids)
            or set(ids) != set(self.required_capability_ids)
            or any(route.instrument not in (None, self.instrument) for route in self.routes)
            or not any(route.asset_mode == "instrument" for route in self.routes)
        ):
            raise ValueError("spot_recipe_asset_routes_incomplete")
        return self

    def resources(self) -> tuple[HashedResource, ...]:
        return (self.recipe, self.pack, self.installed_pointer) + tuple(
            item for route in self.routes for item in (route.manifest, route.route)
        )


class SpotHostReadiness(FrozenContract):
    ready: bool
    checked_at: datetime
    installed_pointer_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    plugin_build_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt: HashedResource


@dataclass(frozen=True)
class SpotTrialAdmission:
    admission_id: str
    research_scope_id: str
    instrument: str
    binding_hash: str
    expires_at: datetime
    resolved_at: datetime
    purpose: Literal["controlled_trial"] = "controlled_trial"
    product_release_qualified: Literal[False] = False
    clock: Callable[[], datetime] = field(default=utcnow, repr=False, compare=False)

    def matches(self, study: StudyRecord) -> bool:
        return (
            study.market == "crypto"
            and study.instrument == self.instrument
            and study.identity_basis != "legacy_unknown"
            and self.research_scope_id == f"{self.instrument.split('-')[0].lower()}-spot-macro.v1"
            and self.resolved_at <= aware_utc(self.clock()) < self.expires_at
        )


class SpotScopeAdmissionService:
    def __init__(
        self,
        database: Database,
        path_store: MarketPathStore,
        *,
        read_resource: Callable[[str], bytes],
        effective_verifier: Callable[[EffectiveSpotRecipe], bool] | None = None,
        route_verifier: Callable[[SpotCapabilityRoute, EffectiveSpotRecipe], bool] | None = None,
        enabled: bool = False,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.database = database
        self.path_store = path_store
        self.read_resource = read_resource
        self.effective_verifier = effective_verifier
        self.route_verifier = route_verifier
        self.enabled = enabled
        self.clock = clock

    def _check_current(
        self,
        effective: EffectiveSpotRecipe,
        host: SpotHostReadiness,
        *,
        assessed_at: datetime | None = None,
    ) -> datetime:
        now = aware_utc(self.clock() if assessed_at is None else assessed_at)
        if not self.enabled:
            raise SpotAdmissionError("spot_research_disabled")
        checked_at = aware_utc(host.checked_at)
        if (
            not host.ready
            or not 0 <= (now - checked_at).total_seconds() < 180
            or host.installed_pointer_hash != effective.installed_pointer.sha256
            or host.plugin_build_hash != effective.plugin_build_hash
        ):
            raise SpotAdmissionError("spot_effective_host_not_ready")
        for resource in (*effective.resources(), host.receipt):
            try:
                actual = hashlib.sha256(self.read_resource(resource.ref)).hexdigest()
            except (OSError, KeyError) as exc:
                raise SpotAdmissionError("spot_effective_resource_unavailable") from exc
            if actual != resource.sha256:
                raise SpotAdmissionError("spot_effective_resource_hash_mismatch")
        # A matching file hash alone does not prove the installed recipe selects it.
        if self.effective_verifier is None or not self.effective_verifier(effective):
            raise SpotAdmissionError("spot_effective_recipe_not_verified")
        if self.route_verifier is None or any(
            not self.route_verifier(route, effective) for route in effective.routes
        ):
            raise SpotAdmissionError("spot_asset_route_implementation_not_verified")
        return now

    def resolve_current_in_session(
        self,
        session: Session,
        *,
        effective: EffectiveSpotRecipe,
        host: SpotHostReadiness,
        admission_id: str | None = None,
        assessed_at: datetime | None = None,
    ) -> VerifiedSpotAdmission:
        """Read a previously approved current proof through the caller writer.

        No approval, HTTP, new session, or write occurs here. New Runs may use
        only this current gate; subsequent reads use the Run's historical proof.
        """
        from .research_run_scope import (
            VerifiedSpotAdmission,
            read_spot_admission_original_in_session,
        )

        now = self._check_current(effective, host, assessed_at=assessed_at)
        if admission_id is None:
            admission_id = session.scalar(
                select(SpotResearchAdmissionRecord.admission_id)
                .where(
                    SpotResearchAdmissionRecord.research_scope_id == effective.research_scope_id,
                    SpotResearchAdmissionRecord.instrument == effective.instrument,
                    SpotResearchAdmissionRecord.binding_hash
                    == canonical_hash(effective.model_dump(mode="json")),
                    SpotResearchAdmissionRecord.created_at <= now,
                    SpotResearchAdmissionRecord.expires_at > now,
                )
                .order_by(
                    SpotResearchAdmissionRecord.created_at.desc(),
                    SpotResearchAdmissionRecord.admission_id.desc(),
                )
                .limit(1)
            )
        if admission_id is None:
            raise SpotAdmissionError("spot_current_admission_unavailable")
        row, proof = read_spot_admission_original_in_session(session, admission_id, admitted_at=now)
        self._resolve_row(
            row, effective, now, canonical_hash(effective.model_dump(mode="json")), self.clock
        )
        return VerifiedSpotAdmission(row.admission_id, row.proof_hash, proof, now)

    def approve_trial(
        self, *, request_id: str, effective: EffectiveSpotRecipe, host: SpotHostReadiness
    ) -> SpotTrialAdmission:
        if not request_id or len(request_id) > 256:
            raise SpotAdmissionError("spot_admission_request_invalid")
        now = self._check_current(effective, host)
        binding_hash = canonical_hash(effective.model_dump(mode="json"))
        with self.database.session() as session:
            previous = session.scalar(
                select(SpotResearchAdmissionRecord).where(
                    SpotResearchAdmissionRecord.request_id == request_id
                )
            )
            if previous:
                return self._resolve_row(previous, effective, now, binding_hash, self.clock)
        scope = spot_scope(effective.instrument)
        due = now.replace(second=0, microsecond=0)
        bars = self.path_store.load(
            scope, start_at=due - timedelta(hours=24, seconds=180), end_at=due, as_of=now
        )
        if not bars or bars[-1].close_at > now or bars[-1].known_at > now:
            raise SpotAdmissionError("spot_current_original_path_not_fresh")
        if (now - bars[-1].close_at).total_seconds() >= 180:
            # This type is issued only after effective Host/route/resource and
            # committed MarketPath originals have passed their existing checks.
            raise SpotSourceFreshnessWait(
                instrument=effective.instrument, scope_id=scope.scope_id,
                binding_hash=binding_hash, assessed_at=now,
                latest_close_at=bars[-1].close_at, latest_known_at=bars[-1].known_at,
                _issuer=_SOURCE_WAIT_ISSUER,
            )
        # A 60s collector may not yet have committed the most recent minute. Use a
        # complete 24h observed window ending at its actual latest closed bar.
        end = bars[-1].close_at
        start = end - timedelta(hours=24)
        bars = tuple(bar for bar in bars if start <= bar.open_at < end)
        path = compute_path_risk(scope, bars, start, end, as_of=now)
        if path.window_status != "complete":
            raise SpotAdmissionError("spot_24h_original_path_not_complete")
        expires_at = min(
            now + timedelta(seconds=180),
            aware_utc(host.checked_at) + timedelta(seconds=180),
            end + timedelta(seconds=180),
        )
        proof = {
            "schema_version": "spot-trial-admission.v1",
            "purpose": "controlled_trial",
            "product_release_qualified": False,
            "effective": effective.model_dump(mode="json"),
            "host": host.model_dump(mode="json"),
            "path": {
                "scope_id": scope.scope_id,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "known_at": max(bar.known_at for bar in bars).isoformat(),
                "semantic_hash": path.semantic_hash,
                "manifest_hash": path.manifest_hash,
                "capture_hashes": sorted(
                    {bar.capture_hash for bar in bars if bar.capture_hash is not None}
                ),
            },
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        proof_json = json.dumps(proof, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        proof_hash = hashlib.sha256(proof_json.encode()).hexdigest()
        row = SpotResearchAdmissionRecord(
            admission_id=f"spottrial_{canonical_hash([request_id, proof_hash])}",
            request_id=request_id,
            research_scope_id=effective.research_scope_id,
            path_scope_id=scope.scope_id,
            instrument=effective.instrument,
            purpose="controlled_trial",
            binding_hash=binding_hash,
            proof_json=proof_json,
            proof_hash=proof_hash,
            created_at=now,
            expires_at=expires_at,
        )
        try:
            with self.database.session() as session:
                session.add(row)
        except IntegrityError:
            # Concurrent retries preserve the winning immutable receipt.
            with self.database.session() as session:
                previous = session.scalar(
                    select(SpotResearchAdmissionRecord).where(
                        SpotResearchAdmissionRecord.request_id == request_id
                    )
                )
                if previous is None:
                    raise
                return self._resolve_row(previous, effective, now, binding_hash, self.clock)
        return self.resolve(row.admission_id, effective=effective, host=host)

    def resolve(
        self, admission_id: str, *, effective: EffectiveSpotRecipe, host: SpotHostReadiness
    ) -> SpotTrialAdmission:
        now = self._check_current(effective, host)
        with self.database.session() as session:
            row = session.get(SpotResearchAdmissionRecord, admission_id)
            if row is None:
                raise SpotAdmissionError("spot_admission_not_found")
            return self._resolve_row(
                row, effective, now, canonical_hash(effective.model_dump(mode="json")), self.clock
            )

    @staticmethod
    def _resolve_row(
        row: SpotResearchAdmissionRecord,
        effective: EffectiveSpotRecipe,
        now: datetime,
        binding_hash: str,
        clock: Callable[[], datetime],
    ) -> SpotTrialAdmission:
        if (
            row.binding_hash != binding_hash
            or row.research_scope_id != effective.research_scope_id
            or row.instrument != effective.instrument
            or row.path_scope_id != spot_scope(effective.instrument).scope_id
            or row.purpose != "controlled_trial"
        ):
            raise SpotAdmissionError("spot_admission_effective_binding_changed")
        if not as_utc(row.created_at) <= now < as_utc(row.expires_at):
            raise SpotAdmissionError("spot_admission_expired")
        if hashlib.sha256(row.proof_json.encode()).hexdigest() != row.proof_hash:
            raise SpotAdmissionError("spot_admission_proof_hash_mismatch")
        proof = json.loads(row.proof_json)
        if (
            proof["effective"] != effective.model_dump(mode="json")
            or proof["purpose"] != "controlled_trial"
            or proof["product_release_qualified"] is not False
            or proof["path"]["scope_id"] != row.path_scope_id
            or aware_utc(datetime.fromisoformat(proof["created_at"])) != as_utc(row.created_at)
            or aware_utc(datetime.fromisoformat(proof["expires_at"])) != as_utc(row.expires_at)
        ):
            raise SpotAdmissionError("spot_admission_proof_identity_mismatch")
        return SpotTrialAdmission(
            row.admission_id,
            row.research_scope_id,
            row.instrument,
            row.binding_hash,
            as_utc(row.expires_at),
            now,
            clock=clock,
        )
