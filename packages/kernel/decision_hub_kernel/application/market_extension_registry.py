"""Register enhanced research routes and verify data admission without asset aliases."""

from __future__ import annotations

from sqlalchemy import select

from ..persistence.db import Database, as_utc, utcnow
from ..persistence.market_case_records import (
    MarketExtensionAdmissionRecord,
    MarketExtensionRegistrationRecord,
)
from ..ports.market_extension import (
    ExtensionAdmission,
    ExtensionOriginalReader,
    GoldMarketSpec,
    InputRouteManifest,
)
from ..ports.market_path import aware_utc, canonical_hash

ROUTE_FIELDS = {
    "MACRO_STATE_V1": ("series_value", "observation_date", "source_public_at", "vintage_at"),
    "CROWDING_V1": (
        "open_interest",
        "open_interest_unit",
        "funding_rate",
        "funding_period",
        "basis",
        "contract",
        "venue",
        "multi_venue_coverage",
    ),
    "EVENT_SURPRISE_V1": (
        "actual_value",
        "consensus_value",
        "prior_value",
        "actual_public_at",
        "consensus_known_at",
        "prior_known_at",
    ),
}


def input_route_manifest(
    route: str, *, scope_id: str, instrument: str, protocol_hash: str, matching_version: str
) -> InputRouteManifest:
    """One fixed candidate per route. Formula registration alone earns no advantage."""
    if route not in ROUTE_FIELDS:
        raise ValueError("market_input_route_unknown")
    return InputRouteManifest(
        route=route,
        scope_id=scope_id,
        instrument=instrument,
        formula_version=f"{route}:source-qualified-features.v1",
        feature_names=ROUTE_FIELDS[route],
        matching_version=matching_version,
        protocol_hash=protocol_hash,
    )


class MarketExtensionRegistry:
    def __init__(
        self,
        database: Database,
        *,
        original_reader: ExtensionOriginalReader | None = None,
        clock=utcnow,
    ):
        self.database, self.original_reader, self.clock = database, original_reader, clock

    def register(self, request_id: str, manifest: GoldMarketSpec | InputRouteManifest) -> str:
        now = aware_utc(self.clock())
        if isinstance(manifest, InputRouteManifest) and (
            manifest.feature_names != ROUTE_FIELDS[manifest.route]
            or manifest.formula_version != f"{manifest.route}:source-qualified-features.v1"
        ):
            raise ValueError("market_input_route_contract_invalid")
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            old = session.scalar(
                select(MarketExtensionRegistrationRecord).where(
                    MarketExtensionRegistrationRecord.request_id == request_id
                )
            )
            if old:
                if self._manifest(old) != manifest:
                    raise ValueError("market_extension_request_reused")
                return old.registration_id
            registration_id = "mext_" + canonical_hash({"request_id": request_id})[:40]
            session.add(
                MarketExtensionRegistrationRecord(
                    registration_id=registration_id,
                    request_id=request_id,
                    scope_id=manifest.scope_id,
                    registered_at=now,
                    manifest_json=manifest.model_dump_json(),
                    manifest_hash=manifest.payload_hash,
                )
            )
            return registration_id

    @staticmethod
    def _manifest(row):
        if row is None:
            raise ValueError("market_extension_registration_missing")
        model = (
            GoldMarketSpec if '"gold-market-spec.v1"' in row.manifest_json else InputRouteManifest
        )
        manifest = model.model_validate_json(row.manifest_json)
        if manifest.payload_hash != row.manifest_hash or manifest.scope_id != row.scope_id:
            raise ValueError("market_extension_manifest_invalid")
        return manifest

    def assess(self, registration_id: str) -> ExtensionAdmission:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(MarketExtensionRegistrationRecord, registration_id)
            manifest = self._manifest(row)
            reasons = []
            original = None
            if isinstance(manifest, GoldMarketSpec):
                if not manifest.provider_id or not manifest.venue:
                    reasons.append("gold_provider_not_selected")
                if not manifest.calendar_ref or not manifest.calendar_hash:
                    reasons.append("gold_calendar_not_verified")
                if manifest.expires_at is not None and manifest.expires_at <= now:
                    reasons.append("gold_future_contract_expired")
            if self.original_reader is None:
                reasons.append("market_extension_original_reader_unavailable")
            else:
                original = self.original_reader.verify(manifest)
                if (
                    original.manifest_hash != manifest.payload_hash
                    or original.scope_id != manifest.scope_id
                    or original.instrument != manifest.instrument
                    or original.known_at > now
                ):
                    raise ValueError("market_extension_original_binding_invalid")
                reasons.extend(original.reasons)
                if not original.original_refs:
                    reasons.append("market_extension_originals_missing")
                if not original.rights_verified:
                    reasons.append("source_rights_unverified")
                if not original.quality_verified:
                    reasons.append("source_quality_unverified")
                if not original.public_time_verified:
                    reasons.append("source_public_time_unknown")
                if not original.mature_labels_verified:
                    reasons.append("mature_labels_unverified")
                if isinstance(manifest, GoldMarketSpec):
                    if (
                        original.provider_id != manifest.provider_id
                        or original.calendar_hash != manifest.calendar_hash
                        or original.price_unit != manifest.price_unit
                        or original.price_basis != manifest.price_basis
                    ):
                        reasons.append("gold_data_scope_mismatch")
                    if not original.calendar_verified:
                        reasons.append("gold_calendar_unverified")
                    missing = {"minute_quote", "complete_path", "mature_label"} - set(
                        original.fields
                    )
                else:
                    missing = set(ROUTE_FIELDS[manifest.route]) - set(original.fields)
                    # Input availability and F5 predictive qualification are separate gates.
                    if (
                        not original.statistical_qualified
                        or original.independent_day_blocks < 100
                        or original.distinct_event_blocks < 30
                    ):
                        reasons.append("f5_independent_sample_qualification_missing")
                reasons.extend(f"required_field_missing:{field}" for field in sorted(missing))
            reasons = tuple(dict.fromkeys(reasons))
            state = "research_qualified" if not reasons else "not_admitted"
            if (
                isinstance(manifest, InputRouteManifest)
                and manifest.route == "MACRO_STATE_V1"
                and original is not None
                and {"series_value", "observation_date"}.issubset(original.fields)
                and original.rights_verified
                and original.quality_verified
                and original.original_refs
            ):
                state = "research_qualified" if not reasons else "descriptive_only"
            admission = ExtensionAdmission(
                registration_id=registration_id,
                manifest_hash=manifest.payload_hash,
                scope_id=manifest.scope_id,
                registered_at=as_utc(row.registered_at),
                checked_at=now,
                state=state,
                reasons=reasons,
                originals=original,
            )
            body = admission.model_dump(mode="json")
            admission_id = "madm_" + canonical_hash(body)[:40]
            old = session.get(MarketExtensionAdmissionRecord, admission_id)
            if old is not None:
                if old.payload_hash != canonical_hash(body):
                    raise ValueError("market_extension_admission_conflict")
                return admission
            session.add(
                MarketExtensionAdmissionRecord(
                    admission_id=admission_id,
                    registration_id=registration_id,
                    checked_at=now,
                    payload_json=admission.model_dump_json(),
                    payload_hash=canonical_hash(body),
                )
            )
            return admission

    def status(self, registration_id: str) -> ExtensionAdmission | None:
        with self.database.session() as session:
            registration = session.get(MarketExtensionRegistrationRecord, registration_id)
            self._manifest(registration)
            row = session.scalar(
                select(MarketExtensionAdmissionRecord)
                .where(
                    MarketExtensionAdmissionRecord.registration_id == registration_id,
                )
                .order_by(
                    MarketExtensionAdmissionRecord.checked_at.desc(),
                    MarketExtensionAdmissionRecord.admission_id,
                )
                .limit(1)
            )
            if row is None:
                return None
            value = ExtensionAdmission.model_validate_json(row.payload_json)
            if (
                canonical_hash(value.model_dump(mode="json")) != row.payload_hash
                or value.registration_id != registration_id
                or value.manifest_hash != registration.manifest_hash
                or value.checked_at != as_utc(row.checked_at)
            ):
                raise ValueError("market_extension_admission_invalid")
            return value
