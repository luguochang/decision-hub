"""Explicit D candidates with real Main dependencies; never fake a source path."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

MAIN = Path("E:/project/study/codex/dsh/decision-hub")
CANDIDATE = Path(__file__).resolve().parents[2]


def load_candidate_module(name: str, relative: str) -> ModuleType:
    """Load the stated physical candidate, retaining its actual __file__."""
    if str(MAIN) in sys.path:
        sys.path.remove(str(MAIN))
    sys.path.insert(0, str(MAIN))
    path = CANDIDATE / relative
    existing = sys.modules.get(name)
    if existing is not None and existing.__file__ is not None:
        if Path(existing.__file__).resolve() == path:
            return existing
    importlib.import_module(name.rpartition(".")[0])
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert module.__file__ is not None and Path(module.__file__).resolve() == path
    return module


def load_review_candidates() -> ModuleType:
    load_candidate_module(
        "packages.contracts_py.decision_hub_contracts.decision_product_v2_contract_base",
        "packages/contracts_py/decision_hub_contracts/decision_product_v2_contract_base.py",
    )
    load_candidate_module(
        "packages.contracts_py.decision_hub_contracts.generated.decision_product_v2",
        "packages/contracts_py/decision_hub_contracts/generated/decision_product_v2.py",
    )
    module = load_candidate_module(
        "packages.runtime_adapters.dsh_runtime.perpetual_attestation",
        "packages/runtime_adapters/dsh_runtime/perpetual_attestation.py",
    )
    dependency = importlib.import_module("packages.runtime_adapters.dsh_runtime.client")
    assert dependency.__file__ is not None
    assert (
        Path(dependency.__file__).resolve()
        == MAIN / "packages/runtime_adapters/dsh_runtime/client.py"
    )
    return module
