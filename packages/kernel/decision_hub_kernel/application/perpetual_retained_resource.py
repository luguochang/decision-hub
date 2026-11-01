"""Finite read-only storage compatibility for original perpetual method inputs.

Original functions recompute every hash/body. No saved-hash substitution, current
Python execution, process Path replacement, or modification of archived bytes.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType

SPEC_REF = "docs/design/decision-product-control-2026-10-17/02_DECISION_METHOD_AND_EVALUATION.md"
SPEC_SHA256 = "c98ee3ad0683741deb09a8e493e98d869f0c38a2b1b0b67ee5bdf4af3c856bb9"
CLOSE_MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_close_source"
BINDING_MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_research_binding"
COST_MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_cost_assumptions"
KERNEL = "packages/kernel/decision_hub_kernel/"
METHOD_FILES = frozenset(
    {
        "decision/action_comparison.py",
        "decision/perpetual_accounting.py",
        "decision/perpetual_publication.py",
        "application/perpetual_cost_assumptions.py",
        "ports/exact_decimal_text.py",
    }
)
COST_FILES = METHOD_FILES - {"decision/perpetual_publication.py"}
MISSING_METHOD_FILES = frozenset(
    {"decision/action_comparison.py", "decision/perpetual_publication.py"}
)


def _failed():
    return ValueError("research_history_perpetual_method_resource_invalid")


def _clone(function, path_type):
    result = FunctionType(
        function.__code__,
        {**function.__globals__, "Path": path_type},
        function.__name__,
        function.__defaults__,
        function.__closure__,
    )
    result.__kwdefaults__ = function.__kwdefaults__
    result.__module__ = function.__module__
    result.__qualname__ = function.__qualname__
    return result


@contextmanager
def perpetual_method_resource_read_scope(host, session, private, run_id, reference_json, kind):
    if kind not in {"round", "final", "publication"} or private.is_v3:
        yield
        return
    reference = json.loads(reference_json)
    if reference.get("row", {}).get("schema_version") not in {
        "research-perpetual-graph-round.v1",
        "research-perpetual-graph-final.v1",
    }:
        yield
        return
    required = {SPEC_REF, *(KERNEL + name for name in MISSING_METHOD_FILES)}
    if required <= private.payloads.keys():
        yield  # New capsules have the real authenticated physical inputs.
        return
    # The cached private modules are shared; the existing RLock covers all old
    # consumer calls and restoration. Every perpetual history entry uses it.
    with host._LOCK:
        terminal = (
            session.connection()
            .exec_driver_sql(
                "SELECT status,artifact_id,finished_at FROM runs WHERE run_id=?", (run_id,)
            )
            .one_or_none()
        )
        if (
            terminal is None
            or terminal[0] not in {"completed", "degraded"}
            or not terminal[1]
            or terminal[2] is None
        ):
            raise PermissionError("research_history_perpetual_resource_not_terminal")
        binding = private.load(BINDING_MODULE)
        saved = binding._read(session, run_id, binding.ADMISSION_ROLE)
        admission = saved[0]
        expected_software = admission["close_window"]["software_hash"]
        expected_comparison = (
            admission["comparison_method"],
            admission["method_ref"],
            admission["method_code_hash"],
        )
        expected_cost = admission["cost_assumptions"]
        dependencies = admission["comparison_method"]["dependencies"]
        if (
            set(dependencies) != METHOD_FILES
            or set(expected_cost["calculator_dependencies"]) != COST_FILES
            or any(
                expected_cost["calculator_dependencies"][name] != dependencies[name]
                for name in COST_FILES
            )
        ):
            raise _failed()
        close = private.load(CLOSE_MODULE)
        cost = private.load(COST_MODULE)
        originals = [
            (close, "software_identity", close.software_identity),
            (binding, "comparison_method_original", binding.comparison_method_original),
            (cost, "_body", cost._body),
        ]
        original_paths = [(module, module.Path) for module, _, _ in originals]
        resources = {}
        candidates = []
        for name in sorted(required):
            if name in private.payloads:
                continue
            physical = Path(host._CURRENT_ROOT) / name
            raw = physical.read_bytes()
            expected = SPEC_SHA256 if name == SPEC_REF else dependencies[name.removeprefix(KERNEL)]
            if hashlib.sha256(raw).hexdigest() != expected:
                raise _failed()
            resources[(Path(private.root) / name).resolve(strict=False)] = raw
            candidates.append((physical, expected))

        class FiniteOriginalPath(type(Path())):
            def read_bytes(self):
                matched = resources.get(self.resolve(strict=False))
                return matched if matched is not None else super().read_bytes()

        isolated = [_clone(function, FiniteOriginalPath) for _, _, function in originals]
        codes = [function.__code__ for _, _, function in originals]

        def verify():
            if any(module.Path is not original_path for module, original_path in original_paths):
                raise _failed()
            for (module, _, function), code in zip(originals, codes, strict=True):
                relative = module._history_original_package + "." + Path(module.__file__).stem
                if (
                    function.__code__ is not code
                    or private.modules.get(relative) is not module
                    or Path(module.__file__).read_bytes()
                    != private.payloads[relative.replace(".", "/") + ".py"]
                ):
                    raise _failed()
            if any(
                hashlib.sha256(path.read_bytes()).hexdigest() != expected
                for path, expected in candidates
            ):
                raise _failed()
            # Identical old source code computes original8files/runtime/spec,
            # complete comparison body/ref/codehash, and complete costs/ref.
            if (
                isolated[0]() != expected_software
                or isolated[1]() != expected_comparison
                or isolated[2]() != expected_cost
                or {"ref": cost.POLICY_REF, "hash": cost.canonical_hash(isolated[2]())}
                != admission["cost_assumptions_ref"]
            ):
                raise _failed()
            if binding._read(session, run_id, binding.ADMISSION_ROLE) != saved:
                raise _failed()

        verify()
        for (module, name, _), function in zip(originals, isolated, strict=True):
            setattr(module, name, function)
        try:
            yield
        finally:
            try:
                if any(
                    getattr(module, name) is not function
                    for (module, name, _), function in zip(originals, isolated, strict=True)
                ):
                    raise _failed()
                verify()
            finally:
                for module, name, original in originals:
                    setattr(module, name, original)
