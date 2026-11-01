"""Compose real installed spot research permissions and fresh source admission.

No client or model field grants this permission. The normal launcher configures
V3 from its validated release; this boundary verifies the actual adapters and
captures an authenticated Host response before approving an immutable trial.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import os
import stat
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from apps.research_mcp.main import compose_capability_adapters, load_capability_manifests
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
    load_prepared_pack_definition,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    INSTALLED_POINTER_REF,
    REGISTRATION_REF,
    _installation,
)
from packages.kernel.decision_hub_kernel.application.risk_admission_wait import observe_owned
from packages.kernel.decision_hub_kernel.application.spot_scope_admission import (
    EffectiveSpotRecipe,
    HashedResource,
    SpotCapabilityRoute,
    SpotHostReadiness,
    SpotScopeAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, as_utc, utcnow
from packages.kernel.decision_hub_kernel.persistence.spot_scope_admission import (
    SpotResearchAdmissionRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash
from packages.provider_adapters.market.spot_research import CAPABILITY, SpotMarketFactsAdapter
from packages.provider_adapters.research import ReplayResearchArchive
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.workbench_adapters.research_catalog import research_query_catalog

MACRO_CAPABILITY = "market.cross_asset"
POLICY_REFS = PreparedResearchPolicyRefs(
    gate_policy_ref="gates/prepared.yaml",
    rubric_policy_ref="evaluations/prepared.yaml",
    role_profile_refs=(
        "profiles/manager.yaml",
        "profiles/counter-thesis.yaml",
        "profiles/data-quality.yaml",
    ),
)


def _bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _data_target(root: Path, path: Path) -> Path:
    """Reject escapes/reparse parents before local immutable publication."""
    root = root.resolve()
    candidate = path.absolute()
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("spot_resource_outside_registered_roots")
    for parent in (candidate, *candidate.parents):
        if parent == root:
            break
        try:
            attributes = getattr(parent.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if parent.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("spot_resource_reparse_parent")
    return candidate


def _publish_original(root: Path, path: Path, body: bytes, *, same_bytes: bool = True) -> bytes:
    """Fsync a private temp, then publish without replacing another process's bytes."""
    path = _data_target(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _data_target(root, path)
    if path.exists():
        actual = path.read_bytes()
    else:
        temporary = _data_target(root, path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp"))
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            _data_target(root, path)
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
            actual = path.read_bytes()
        finally:
            temporary.unlink(missing_ok=True)
    if same_bytes and actual != body:
        raise ValueError("spot_resource_original_corrupt")
    return actual


def _host_checked_at(raw: object, *, plugin_build_hash: str, now: datetime) -> datetime:
    """Validate external JSON before accessing nested fields; no unhandled shapes."""
    if not isinstance(raw, dict) or raw.get("schema_version") != "dsh-host-readiness.v1":
        raise ValueError("spot_effective_host_response_invalid")
    identity = raw.get("upstream_identity")
    checked = raw.get("checked_at")
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("plugin_build_hash"), str)
        or not isinstance(checked, str)
        or not isinstance(raw.get("ready"), bool)
        or not isinstance(raw.get("version_compatible"), bool)
    ):
        raise ValueError("spot_effective_host_response_invalid")
    build_hash = identity["plugin_build_hash"]
    if len(build_hash) != 64 or any(item not in "abcdef0123456789" for item in build_hash):
        raise ValueError("spot_effective_host_response_invalid")
    try:
        checked_at = aware_utc(datetime.fromisoformat(checked.replace("Z", "+00:00")))
    except (ValueError, TypeError) as error:
        raise ValueError("spot_effective_host_response_invalid") from error
    if (
        raw["ready"] is not True
        or raw["version_compatible"] is not True
        or identity["plugin_build_hash"] != plugin_build_hash
        or not 0 <= (aware_utc(now) - checked_at).total_seconds() < 180
    ):
        raise ValueError("spot_effective_host_not_ready")
    return checked_at


class InstalledSpotResearch:
    def __init__(
        self,
        database: Database,
        data_root: Path,
        *,
        enabled: bool = False,
        clock: Callable[[], datetime] = utcnow,
        host_url: str = "http://127.0.0.1:3097/decision-hub/v1/readiness",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.root = Path(__file__).resolve().parents[2]
        self.data_root = data_root.resolve()
        self.enabled, self.clock, self.host_url, self.transport = (
            enabled,
            clock,
            host_url,
            transport,
        )
        self.allowed = tuple(
            x.strip()
            for x in os.getenv(
                "DECISION_HUB_RESEARCH_CAPABILITIES",
                "",
            ).split(",")
            if x.strip()
        )
        self.effective: dict[str, EffectiveSpotRecipe] = {}
        self.host: SpotHostReadiness | None = None
        self.admission_ids: dict[str, str] = {}
        data = MarketRiskDataService(
            database,
            RawBodyArchive(data_root / "raw-captures"),
            data_root / "market-risk/features",
            enabled=False,
            clock=clock,
        )
        self.spot = SpotMarketFactsAdapter(data, read_enabled=enabled, clock=clock)
        self.manifests = {item.capability_id: item for item in load_capability_manifests()}
        self.adapters = compose_capability_adapters(
            list(self.manifests.values()),
            ReplayResearchArchive({}, {}),
            spot_market_adapter=self.spot,
        )
        self.catalog = research_query_catalog(
            list(self.manifests.values()), self.adapters, set(self.allowed)
        )
        self.key = b""
        if enabled:
            key_path = self.data_root / "spot-admissions/route-signing.key"
            self.key = _publish_original(
                self.data_root,
                key_path,
                os.urandom(32),
                same_bytes=False,
            )
            if len(self.key) != 32:
                raise ValueError("spot_route_signing_key_invalid")
        self.service = SpotScopeAdmissionService(
            database,
            data.store,
            read_resource=self.read,
            effective_verifier=self._verify_effective,
            route_verifier=self._verify_route,
            enabled=enabled,
            clock=clock,
        )

    def read(self, ref: str) -> bytes:
        path = Path(ref)
        path = (path if path.is_absolute() else self.root / path).resolve()
        if not any(path.is_relative_to(base) for base in (self.root, self.data_root)):
            raise ValueError("spot_resource_outside_registered_roots")
        return path.read_bytes()

    def _resource(self, path: Path) -> HashedResource:
        path = path.resolve()
        ref = (
            path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)
        )
        return HashedResource(ref=ref, sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def _store(self, value: object) -> HashedResource:
        body = _bytes(value)
        digest = hashlib.sha256(body).hexdigest()
        path = self.data_root / "spot-admissions/originals" / f"{digest}.json"
        _publish_original(self.data_root, path, body)
        return self._resource(path)

    def _macro_material(self) -> dict[str, object]:
        adapter = next(
            (item for item in self.adapters if item.capability_id == MACRO_CAPABILITY), None
        )
        row = next(
            (x for x in self.catalog["capabilities"] if x["capability_id"] == MACRO_CAPABILITY),
            None,
        )
        if adapter is None or row is None or not row["routes"]:
            raise ValueError("spot_macro_adapter_not_registered")
        objects = [adapter, *getattr(adapter, "adapters", {}).values()]
        classes = []
        for item in objects:
            source = inspect.getsourcefile(type(item))
            if source is None:
                raise ValueError("spot_macro_adapter_source_unavailable")
            classes.append(
                {
                    "class": f"{type(item).__module__}.{type(item).__qualname__}",
                    "source": self._resource(Path(source)).model_dump(mode="json"),
                }
            )
        return {
            "schema_version": "spot-macro-route.v1",
            "asset_mode": "global_macro",
            "capability_id": MACRO_CAPABILITY,
            "catalog": row,
            "implementations": classes,
        }

    def _signed_macro(self) -> dict[str, object]:
        body = self._macro_material()
        return {
            "body": body,
            "signature": hmac.new(self.key, _bytes(body), hashlib.sha256).hexdigest(),
        }

    def _compose_effective(self, instrument: str) -> EffectiveSpotRecipe:
        installation = _installation()
        pointer = self._resource(self.root / INSTALLED_POINTER_REF)
        if pointer.sha256 != installation.pointer.sha256:
            raise ValueError("spot_installed_pointer_changed")
        pack_root = self.root / "packs/crypto_macro_prepared/1.2.0"
        load_prepared_pack_definition(
            pack_root,
            POLICY_REFS,
            allowed_capabilities=self.allowed,
            execution_mode="live",
        )
        # Admission and the Task factory authenticate the same installed manifest
        # bytes. A serialized prepared definition is a different original.
        pack_resource = self._resource(pack_root / "pack.yaml")
        spot_manifest = self.manifests[CAPABILITY]
        routes = (
            SpotCapabilityRoute(
                capability_id=CAPABILITY,
                asset_mode="instrument",
                instrument=instrument,
                manifest=self._store(spot_manifest.model_dump(mode="json")),
                route=self._store(
                    self.spot.signed_route_receipt(
                        instrument,
                        spot_manifest,
                        signing_key=self.key,
                    )
                ),
            ),
            SpotCapabilityRoute(
                capability_id=MACRO_CAPABILITY,
                asset_mode="global_macro",
                manifest=self._store(self.manifests[MACRO_CAPABILITY].model_dump(mode="json")),
                route=self._store(self._signed_macro()),
            ),
        )
        return EffectiveSpotRecipe(
            research_scope_id=f"{instrument.split('-')[0].lower()}-spot-macro.v1",
            instrument=instrument,
            recipe=self._resource(self.root / REGISTRATION_REF),
            pack=pack_resource,
            installed_pointer=pointer,
            plugin_build_hash=installation.plugin_build_hash,
            required_capability_ids=(CAPABILITY, MACRO_CAPABILITY),
            routes=routes,
        )

    def _verify_effective(self, effective: EffectiveSpotRecipe) -> bool:
        try:
            return (
                self.enabled
                and effective == self.effective.get(effective.instrument)
                and effective == self._compose_effective(effective.instrument)
                and {CAPABILITY, MACRO_CAPABILITY}.issubset(self.allowed)
            )
        except (ValueError, KeyError, OSError):
            return False

    def _verify_route(self, route: SpotCapabilityRoute, effective: EffectiveSpotRecipe) -> bool:
        if route.capability_id == CAPABILITY:
            return self.spot.verify_admission_route(
                route,
                effective,
                read_resource=self.read,
                signing_key=self.key,
            )
        if route.capability_id != MACRO_CAPABILITY or route.asset_mode != "global_macro":
            return False
        try:
            manifest = json.loads(self.read(route.manifest.ref))
            raw = json.loads(self.read(route.route.ref))
            expected = self._signed_macro()
            return (
                manifest == self.manifests[MACRO_CAPABILITY].model_dump(mode="json")
                and raw["body"] == expected["body"]
                and hmac.compare_digest(raw["signature"], expected["signature"])
            )
        except (ValueError, KeyError, OSError, TypeError):
            return False

    async def prepare(self, instrument: str) -> None:
        if not self.enabled:
            raise ValueError("spot_research_disabled")
        effective = await observe_owned(asyncio.to_thread(self._compose_effective, instrument))
        self.effective[instrument] = effective
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=5, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.get(self.host_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError("spot_effective_host_unavailable") from exc
        try:
            raw = response.json()
        except ValueError as error:
            raise ValueError("spot_effective_host_response_invalid") from error
        now = aware_utc(self.clock())
        checked_at = _host_checked_at(raw, plugin_build_hash=effective.plugin_build_hash, now=now)
        capture = {
            "schema_version": "spot-host-readiness-capture.v1",
            "requested_url": self.host_url,
            "received_at": now.isoformat(),
            "http_status": response.status_code,
            "response": raw,
        }
        signed_capture = {
            "body": capture,
            "signature": hmac.new(self.key, _bytes(capture), hashlib.sha256).hexdigest(),
        }
        self.host = SpotHostReadiness(
            ready=True,
            checked_at=checked_at,
            installed_pointer_hash=effective.installed_pointer.sha256,
            plugin_build_hash=effective.plugin_build_hash,
            receipt=await observe_owned(asyncio.to_thread(self._store, signed_capture)),
        )
        request_hash = canonical_hash([now.isoformat(), effective.model_dump(mode="json")])
        trial = await observe_owned(asyncio.to_thread(
            self.service.approve_trial,
            request_id=f"spot-runtime:{instrument}:{request_hash}",
            effective=effective,
            host=self.host,
        ))
        self.admission_ids[instrument] = trial.admission_id

    def resolve_in_session(self, session, study, assessed_at):
        instrument = study.instrument
        effective = self._compose_effective(instrument)
        self.effective[instrument] = effective
        original = session.scalar(
            select(SpotResearchAdmissionRecord)
            .where(
                SpotResearchAdmissionRecord.instrument == instrument,
                SpotResearchAdmissionRecord.binding_hash
                == canonical_hash(effective.model_dump(mode="json")),
                SpotResearchAdmissionRecord.created_at <= assessed_at,
                SpotResearchAdmissionRecord.expires_at > assessed_at,
            )
            .order_by(SpotResearchAdmissionRecord.created_at.desc())
            .limit(1)
        )
        if original is None:
            raise ValueError("spot_current_admission_not_prepared")
        try:
            host = SpotHostReadiness.model_validate(json.loads(original.proof_json)["host"])
            original_bytes = self.read(host.receipt.ref)
            if hashlib.sha256(original_bytes).hexdigest() != host.receipt.sha256:
                raise ValueError("spot_host_capture_not_authenticated")
            signed = json.loads(original_bytes)
            capture = signed["body"]
            received = aware_utc(datetime.fromisoformat(capture["received_at"]))
            expected = hmac.new(self.key, _bytes(capture), hashlib.sha256).hexdigest()
            checked = _host_checked_at(
                capture["response"],
                plugin_build_hash=effective.plugin_build_hash,
                now=aware_utc(assessed_at),
            )
            if (
                not hmac.compare_digest(signed["signature"], expected)
                or capture["schema_version"] != "spot-host-readiness-capture.v1"
                or capture["requested_url"] != self.host_url
                or type(capture["http_status"]) is not int
                or capture["http_status"] != 200
                or checked != aware_utc(host.checked_at)
                or not checked <= received <= aware_utc(assessed_at)
                or received > as_utc(original.created_at)
            ):
                raise ValueError("spot_host_capture_not_authenticated")
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as error:
            raise ValueError("spot_host_capture_not_authenticated") from error
        return self.service.resolve_current_in_session(
            session,
            admission_id=original.admission_id,
            effective=effective,
            host=host,
            assessed_at=assessed_at,
        )
