"""Declare installed functions at startup; registration grants no approval."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import cast

from apps.hub_worker.market_component_originals import ProductionComponentOriginalReader
from apps.hub_worker.market_component_research_protocol import RecipeOriginalProtocol
from apps.hub_worker.market_component_trace_protocols import (
    MonitorRuleOriginalProtocol,
    SourceParserOriginalProtocol,
)
from apps.hub_worker.market_component_versions import load_component_versions
from apps.hub_worker.market_components import InstalledComponent, MarketComponents
from packages.kernel.decision_hub_kernel.application.component_feature_protocol import (
    FeatureOriginalProtocol,
)
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.application.forecast_alarm import ForecastAlarmService
from packages.kernel.decision_hub_kernel.application.market_component_functions import (
    build_market_feature_component,
    evaluate_market_rule_component,
    normalize_market_path_component,
)
from packages.kernel.decision_hub_kernel.application.market_component_research_functions import (
    PATH_COMPONENT_INPUT,
    PATH_COMPONENT_OUTPUT,
    RECIPE_COMPONENT_INPUT,
    RECIPE_COMPONENT_OUTPUT,
    RECIPE_COMPONENT_REF,
    predict_registered_path_forecast,
    select_registered_spot_research_recipe,
)
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import RiskPathLedger
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentKind
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope
from packages.provider_adapters.research.raw_capture import RawBodyArchive


def declare_market_components() -> tuple[InstalledComponent, ...]:
    """Declare current pure functions without DB writes or provider calls."""
    root = Path(__file__).resolve().parents[2]
    kernel = "packages/kernel/decision_hub_kernel/"
    definitions = (
        (
            "source_parser",
            "okx.spot_path.v1",
            "market-path-component-input.v1",
            "market-price-path.v1",
            normalize_market_path_component,
            (
                "application/market_component_functions.py",
                "ports/market_path.py",
                "application/market_features.py",
                "application/risk_monitor.py",
                "decision/path_risk.py",
            ),
        ),
        (
            "feature",
            "spot.features.v1",
            "market-feature-component-input.v1",
            "market-feature-snapshot.v1",
            build_market_feature_component,
            (
                "application/market_component_functions.py",
                "application/market_features.py",
                "decision/path_risk.py",
                "ports/market_path.py",
            ),
        ),
        (
            "monitor_rule",
            "spot.move30.v1",
            "market-rule-component-input.v1",
            "market-rule-component-output.v1",
            evaluate_market_rule_component,
            (
                "application/market_component_functions.py",
                "application/risk_monitor.py",
                "ports/risk_monitor.py",
                "ports/market_path.py",
            ),
        ),
        *(
            (
                "model",
                method,
                PATH_COMPONENT_INPUT,
                PATH_COMPONENT_OUTPUT,
                predict_registered_path_forecast,
                (
                    "application/market_component_research_functions.py",
                    "decision/path_forecast.py",
                    "ports/path_forecast.py",
                    "application/market_features.py",
                    "ports/market_path.py",
                ),
            )
            for method in ("BASE_EMPIRICAL_V1", "COND_VOL_V1")
        ),
        (
            "research_recipe",
            RECIPE_COMPONENT_REF,
            RECIPE_COMPONENT_INPUT,
            RECIPE_COMPONENT_OUTPUT,
            select_registered_spot_research_recipe,
            (
                "application/market_component_research_functions.py",
                "application/research_binding.py",
                "application/spot_scope_admission.py",
                "application/research_graph_recipe_v3.py",
            ),
        ),
    )
    installed: list[InstalledComponent] = []
    for instrument in ("BTC-USDT", "ETH-USDT"):
        scope = spot_scope(instrument)
        for kind, ref, input_contract, output_contract, callback, dependencies in definitions:
            files = {kernel + name: root / kernel / name for name in dependencies}
            # Identity covers actual source bytes. Both workers register the same
            # immutable declaration; neither activates a pointer or a shadow review.
            identity = canonical_hash(
                {
                    "kind": kind,
                    "ref": ref,
                    "scope_id": scope.scope_id,
                    "input": input_contract,
                    "output": output_contract,
                    "files": {
                        name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for name, path in files.items()
                    },
                    "parameters": {},
                }
            )
            installed.append(
                InstalledComponent(
                    kind=cast(ComponentKind, kind),
                    component_ref=ref,
                    scope_id=scope.scope_id,
                    version_id="installed_" + identity,
                    input_contract=input_contract,
                    output_contract=output_contract,
                    callback=callback,
                    code_files=files,
                )
            )
    return tuple(installed)


def build_market_components(database: Database) -> MarketComponents:
    installed = declare_market_components()
    receipt_path = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT")
    receipt_hash = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256")
    if bool(receipt_path) != bool(receipt_hash):
        raise ValueError("component_installed_release_binding_incomplete")
    if receipt_path and receipt_hash:
        root = Path(__file__).resolve().parents[2]
        raw = Path(receipt_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt_hash:
            raise ValueError("component_installed_release_receipt_changed")
        receipt = json.loads(raw)
        archives = (
            receipt.get("evidence", {}).get("retained_market_components_v1", {}).get("archives", [])
        )
        if not isinstance(archives, list):
            raise ValueError("component_installed_release_archives_invalid")
        for archive_ref in archives:
            if not isinstance(archive_ref, dict) or set(archive_ref) != {
                "index_ref",
                "index_sha256",
            }:
                raise ValueError("component_installed_release_archive_ref_invalid")
            installed += load_component_versions(
                repository_root=root,
                validated_receipt_path=Path(receipt_path),
                validated_receipt_sha256=receipt_hash,
                index_ref=archive_ref["index_ref"],
                index_sha256=archive_ref["index_sha256"],
                already_installed=installed,
            )
    archive = RawBodyArchive(
        Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "raw-captures",
    )
    path_store = MarketPathStore(database, archive)
    forecast_alarms = ForecastAlarmService(database, path_store)
    original_reader = ProductionComponentOriginalReader(
        database,
        tuple(installed),
        archive,
        protocol_handlers=(
            SourceParserOriginalProtocol(),
            FeatureOriginalProtocol(),
            MonitorRuleOriginalProtocol(),
            RecipeOriginalProtocol(),
        ),
        path_ledger=RiskPathLedger(
            database,
            path_store,
            forecast_alarm_original_reader=forecast_alarms.read_score_in_session,
        ),
    )
    consumers = MarketComponents(
        ComponentRegistry(database, original_reader=original_reader),
        tuple(installed),
    )
    consumers.register_installed()
    return consumers
