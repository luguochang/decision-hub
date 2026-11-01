"""Install finite immutable baseline packages; selection authenticates each Run anew."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from apps.hub_worker.market_component_bootstrap import (
    build_market_components,
    declare_market_components,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
)
from packages.provider_adapters.research import ReplayResearchArchive
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from apps.hub_worker.frozen_skill_component import FrozenSkillComponentConsumer
from apps.research_mcp.capability_composition import build_research_capability_composition
from apps.research_mcp.main import load_capability_manifests
from packages.kernel.decision_hub_kernel.application import research_skill_compiler as c
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.research_skill_consumption import (
    SkillConsumptionService,
)
from packages.kernel.decision_hub_kernel.application.research_skill_nodes import SkillNodeService
from packages.kernel.decision_hub_kernel.application.research_skill_package import (
    InstalledSkillCompiler,
    SkillContracts,
    SkillPackageService,
    decoded,
    encoded,
    sha,
)
from packages.kernel.decision_hub_kernel.application.research_skill_preparation import (
    SkillPreparation,
)
from packages.kernel.decision_hub_kernel.application.research_skill_seed import causal_seed
from packages.kernel.decision_hub_kernel.application.research_skill_selection import (
    SkillSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_skill_service import (
    ResearchSkillService,
)
from packages.kernel.decision_hub_kernel.application.research_skill_use import SkillUseService
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.provider_adapters.market.spot_research import CODE_FILES, VERSION


def _catalog_scope(catalog, identity, allowed):
    capabilities = []
    for item in catalog["capabilities"]:
        if item["capability_id"] not in allowed or "live" not in item["modes"]:
            continue
        capabilities.append(
            {
                **item,
                "modes": ["live"],
                "routes": [
                    {**route, "modes": ["live"]}
                    for route in item["routes"]
                    if "live" in route["modes"]
                ],
            }
        )
    value = {
        "schema_version": catalog["schema_version"],
        "capabilities": capabilities,
        "scope": identity,
    }
    # Same existing catalog codec (ensure_ascii=True), including all admitted
    # capabilities and the exact actual Run identity, not a required-only hash.
    value["catalog_hash"] = sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    return value


class InstalledResearchSkills:
    def __init__(self, database, data_root: Path, *, components=None, clock=None):
        from packages.kernel.decision_hub_kernel.persistence.db import utcnow

        self.clock = clock or utcnow
        self.root = Path(__file__).resolve().parents[2]
        self.database = database
        self.components = components or build_market_components(database)
        archive = RawBodyArchive(data_root / "raw-captures")
        allowed = {
            x.strip()
            for x in os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES", "").split(",")
            if x.strip()
        }
        self.capabilities = build_research_capability_composition(
            database,
            data_root,
            allowed,
            manifests=load_capability_manifests(),
            archive=ReplayResearchArchive({}, {}),
        )
        app = self.root / "packages/kernel/decision_hub_kernel/application"
        closure = {
            path.relative_to(self.root).as_posix(): path for path in app.glob("research_skill_*.py")
        }
        for relative in (
            *CODE_FILES,
            "apps/hub_worker/frozen_skill_component.py",
            "apps/hub_worker/research_skill_bootstrap.py",
            "apps/research_mcp/capability_composition.py",
            "apps/research_mcp/main.py",
            "contracts/schemas/research_skill.schema.yaml",
        ):
            closure[relative] = self.root / relative
        self.installed = InstalledSkillCompiler.capture(closure)
        self.packages = SkillPackageService(
            self.components.registry, archive, self.installed, SkillContracts(), clock=self.clock
        )
        self.contexts, self.baselines, self.versions = {}, {}, {}
        features = [
            x.manifest()
            for x in declare_market_components()
            if x.component_ref == "spot.features.v1"
        ]
        for feature in features:
            if feature.version_id not in self.components.installed:
                raise ValueError("skill_current_feature_not_installed")
            context, files, version_id = self._author_context(feature)
            author = data_root / "research-skills/packages" / version_id
            for resource in files:
                path = author / resource.path
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with path.open("xb") as stream:
                        stream.write(resource.body)
                except FileExistsError:
                    if path.read_bytes() != resource.body:
                        raise ValueError("skill_author_package_bytes_changed") from None
            prepared = self.packages.prepare(
                author, skill_id="causal-synthesis", version_id=version_id, context=context
            )
            manifest = self.packages.register(prepared)
            self.contexts[feature.scope_id] = context
            self.baselines[version_id] = manifest.payload_hash
            self.versions[feature.scope_id] = (
                version_id,
                datetime.fromisoformat(prepared.version["first_known_at"]),
            )
        self.selections = SkillSelectionService(
            self.packages,
            baseline_manifests=self.baselines,
            context_reader=self.context_in_session,
            clock=self.clock,
        )
        use = SkillUseService(self.selections, clock=self.clock)
        nodes = SkillNodeService(
            use, MarketPathStore(database, archive), FrozenSkillComponentConsumer(self.components)
        )
        self.consumption = SkillConsumptionService(nodes)
        self.service = ResearchSkillService(self.consumption)
        self.preparation = SkillPreparation(self.selections, self.versions)

    def _author_context(self, feature):
        entry = next(
            (
                x
                for x in self.capabilities.catalog["capabilities"]
                if x["capability_id"] == "spot_market_facts"
            ),
            None,
        )
        if entry is None:
            raise ValueError("skill_spot_capability_not_enabled")
        adapter_hash = canonical_hash(
            {name: sha((self.root / name).read_bytes()) for name in CODE_FILES}
        )
        version_id = (
            "skill_causal_"
            + canonical_hash(
                [feature.scope_id, feature.payload_hash, adapter_hash, self.installed.code_hash]
            )[:40]
        )
        files, schemas, _ = causal_seed(
            scope_id=feature.scope_id,
            version_id=version_id,
            surface_hash="0" * 64,
            feature_manifest=feature,
        )
        by_ref = {x.reference: x for x in schemas}
        route = {
            "provider_id": None,
            "modes": ["live"],
            "fields": ["spot_price", "return_24h", "path"],
            "symbols": [feature.scope_id.split(":")[3]],
            "allowed_domains": ["www.okx.com"],
            "requires_event_window": False,
        }
        surface = c.CapabilitySurface(
            "research-capability-invocation.v1",
            VERSION,
            adapter_hash,
            by_ref["skill-gateway-intent.v1"].schema_hash,
            by_ref["research-capability-result.v1"].schema_hash,
            (feature.scope_id,),
            encoded({"spot_price": "usdt", "return_24h": "fraction", "path": "path_manifest"}),
            encoded([route]),
        )
        cap = c.CapabilityBinding(
            "spot_market_facts",
            "skill-gateway-intent.v1",
            "research-capability-result.v1",
            canonical_hash(entry),
            surface,
        )
        files, schemas, _ = causal_seed(
            scope_id=feature.scope_id,
            version_id=version_id,
            surface_hash=c.required_capability_surface_hash((cap,)),
            feature_manifest=feature,
        )
        context = c.CompilerContext(
            feature.scope_id,
            "live",
            self.installed.code_hash,
            "schemas/workflow.schema.json",
            next(x.sha256 for x in files if x.path == "schemas/workflow.schema.json"),
            schemas,
            (
                c.InstalledOperation(
                    "evidence.acquire",
                    "skill-gateway-intent.v1",
                    "research-capability-result.v1",
                    adapter_hash,
                ),
                c.InstalledOperation(
                    "component.compute",
                    feature.input_contract,
                    feature.output_contract,
                    feature.code_hash,
                ),
                c.InstalledOperation(
                    "hypothesis.check",
                    "skill-causal-hypothesis-input.v1",
                    "research-skill-hypothesis-output.v1",
                    self.installed.code_hash,
                ),
            ),
            (cap,),
            (c.InstalledComponent(feature),),
            encoded(self.capabilities.catalog),
            frozenset({"spot_market_facts"}),
            None,
        )
        return context, files, version_id

    def context_in_session(self, session, execution):
        self.installed.verify()
        prepared = read_prepared_execution_in_session(
            session, execution.run_id, execution.generation
        )
        binding = read_run_spot_admission_in_session(session, execution.run_id)
        if binding is None:
            raise ValueError("skill_frozen_spot_scope_required")
        identity = decoded(execution.scope_identity_json)
        if (
            prepared.method.method_hash != execution.method_hash
            or prepared.execution.execution_request_hash != identity["request_hash"]
        ):
            raise ValueError("skill_prepared_catalog_identity_mismatch")
        context = self.contexts[binding.scope.scope_id]
        catalog = _catalog_scope(
            self.capabilities.catalog, identity, prepared.execution.request.allowed_capabilities
        )
        entry = next(
            (x for x in catalog["capabilities"] if x["capability_id"] == "spot_market_facts"), None
        )
        if entry is None:
            raise ValueError("skill_spot_capability_not_admitted")
        return replace(
            context,
            catalog_json=encoded(catalog),
            execution_identity_json=encoded(identity),
            capabilities=(
                replace(context.capabilities[0], catalog_entry_hash=canonical_hash(entry)),
            ),
            allowed_capabilities=frozenset(prepared.execution.request.allowed_capabilities),
        )


def configured_research_skills(database, data_root, *, components=None):
    flag = os.getenv("DECISION_HUB_RESEARCH_SKILLS_ENABLED", "0")
    if flag not in {"0", "1"}:
        raise ValueError("skill_enable_flag_invalid")
    if flag == "0":
        return None
    if (
        os.getenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "0") != "1"
        or os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") != "1"
    ):
        raise ValueError("skill_action_spot_product_required")
    return InstalledResearchSkills(database, Path(data_root), components=components)
