"""Registered, read-only original Context Selection implementations.

Resources are reviewed release artifacts, never caller-provided Python. The
original producer and its original Graph reader execute their retained bytes.
Unchanged dependencies are reused only while their retained identity matches.
No old implementation can create a Selection or obtain execution permission.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import re
import sys
import threading
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

_VERSION = "e753356adb9df1f8fdef0695f71662afbc43090eb10a1421d13fd0fbff52f47c"
_ROOT = Path(__file__).resolve().parents[4]
_RESOURCE_ROOT = Path(__file__).resolve().parent / "context_selector_versions" / "e753356a"
_PREFIX = "packages.kernel.decision_hub_kernel.application."
_SELECTION = "packages/kernel/decision_hub_kernel/application/research_context_selection.py"
_BINDING = "packages/kernel/decision_hub_kernel/application/research_graph_binding.py"
_CONTRACT_BASE = (
    "packages/contracts_py/decision_hub_contracts/context_selection_v3_contract_base.py"
)
_GENERATED = (
    "packages/contracts_py/decision_hub_contracts/generated/research_context_selection_v3.py"
)
_SCHEMA = "contracts/schemas/research_context_selection_v3.schema.yaml"
_PRIVATE_SOURCE_FILES = (_CONTRACT_BASE, _GENERATED, _BINDING, _SELECTION)
_PRIVATE_FILES = frozenset((*_PRIVATE_SOURCE_FILES, _SCHEMA))

_FILES = {
    "packages/kernel/decision_hub_kernel/application/research_context.py": (
        "fc79378812cd31b42346bfdd8fa7ce227cf65d06b0e63f704c2ef7a8823358f7"
    ),
    "packages/kernel/decision_hub_kernel/application/research_context_selector_v2.py": (
        "70e1db32312a95edf819df9c6a06c9491a35281a469fd62a5d8c80c5e4e57c33"
    ),
    _SELECTION: "d081e78665ef6c5014080eb880613e40d65ac9ce471b7c58adb0d8ae76436b7d",
    "packages/kernel/decision_hub_kernel/application/research_graph_projection.py": (
        "c08525b931e5c9078244afb2414321d59fe68c2938ebdee34365079ea6a36f5c"
    ),
    _BINDING: "a596b58b9180d5c58417357499f3f1b351ef1b67b9e332619ab506a1a4471f4a",
    "packages/kernel/decision_hub_kernel/application/research_binding.py": (
        "f78b6d6bb902cf5c21d6b7b28f7fabf777efb08d0d3b43c0403ed616881a5102"
    ),
    "packages/kernel/decision_hub_kernel/application/research_execution_binding.py": (
        "54dbbe955998a5b5704badff865c30f4db2a62dce118ff6fc920ce5f8f658be6"
    ),
    "packages/contracts_py/decision_hub_contracts/context_selection_semantics.py": (
        "b6a022c54f95f5385e040ad93a5d285151de04e1d92969514be7a906ee452bb1"
    ),
    "packages/contracts_py/decision_hub_contracts/context_selection_v3_contract_base.py": (
        "9a31dfd029357ca0846f43e6e7b7b962b24b0d7e9a55e148059fe8fa3c04426b"
    ),
    "packages/contracts_py/decision_hub_contracts/generated/agentic_research.py": (
        "09d5d5a8db29010acb85462587f2d31abc6fe14310b64a751ac402e9b2f09b10"
    ),
    "packages/contracts_py/decision_hub_contracts/generated/research_context_selection_v3.py": (
        "3d2dfaad2e95e3274b587662036fef5ca9845f63057c3f94ad3e30185e9c1f71"
    ),
    "contracts/schemas/research_context_selection_v3.schema.yaml": (
        "c4abb8cfca8de1b47b6783d8331bf4a06aa5a5ec7796e0b20dce37eeb7b235ce"
    ),
}
# Complete original Base3 registry supplements, separate from the original
# e753 12-file identity. These exact bytes match the sealed release proof.
_AUXILIARY_SCHEMAS = {
    "contracts/schemas/research_context_selection_v2.schema.yaml": (
        "7762be69bc73965153bfeb43c54518d9899ecce18661f3b908d20d41c1b355db"
    ),
    "contracts/schemas/research_context_selection.schema.yaml": (
        "908c58328a9330e3e94aa656688c5e71c2bb79208cadf39efd96e9e2ae62ebf9"
    ),
    "contracts/schemas/research_execution_binding.schema.yaml": (
        "148173eef4b8ac9aa9f5dddb31e1f49efd6b93b104d8250b1e7736e8aca015d6"
    ),
    "contracts/schemas/research_policy.schema.yaml": (
        "ead7410a8ea1d324c117aa5aeb0a6e08723802662ba45a2ce34a8d002d51103d"
    ),
    "contracts/schemas/agentic_research.schema.yaml": (
        "c162ff69788ce9fabe887a6e06cbf85298d1c41ba5ced5c1ad2634c56986aaba"
    ),
    "contracts/schemas/dsh_host_bridge.schema.yaml": (
        "1349581e62d42eadc233ac3ad823b24f32f50687e7e6d8fbbd881e475f53989a"
    ),
    "contracts/schemas/research_fact.schema.yaml": (
        "f63a0f6abcfaf43a53e5ca2378cbd743dad95c4e9d0641667a2662f9cacf1401"
    ),
}
_IDENTITY = {
    "time_rule_ref": "research-context.received-cutoff.v1",
    "ranking_rules_ref": "research-context.relevance-authority-recency.v2",
    "context_policy_source_hash": _FILES[
        "packages/kernel/decision_hub_kernel/application/research_context.py"
    ],
    "producer_source_hash": _FILES[_SELECTION],
    "implementation_hash": _VERSION,
    "implementation_hash_kind": "canonical_context_selector_sources.v1",
}
_LOCK = threading.RLock()
_MODULES: dict[str, ModuleType] = {}
_READ_STACK: ContextVar[tuple[tuple[int, str, str], ...]] = ContextVar(
    "retained_context_selection_reads", default=()
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unavailable() -> ValueError:
    return ValueError("context_selection_implementation_unavailable")


def _verified_resources() -> dict[str, bytes]:
    """Check each read; a previously loaded module never bypasses changed files."""
    identity_hash = hashlib.sha256(
        _canonical({"files": _FILES, "codec": "canonical_context_selection.v3"}).encode()
    ).hexdigest()
    if identity_hash != _VERSION:
        raise _unavailable()
    originals: dict[str, bytes] = {}
    for relative, expected in _FILES.items():
        try:
            payload = (_RESOURCE_ROOT / relative).read_bytes()
            if hashlib.sha256(payload).hexdigest() != expected:
                raise _unavailable()
            # Four implementations and the V3 schema execute privately.
            # Shared generation proof dependencies still use exact originals.
            if relative not in _PRIVATE_FILES:
                current = (_ROOT / relative).read_bytes()
                if hashlib.sha256(current).hexdigest() != expected:
                    raise _unavailable()
        except OSError as exc:
            raise _unavailable() from exc
        originals[relative] = payload
    for relative, expected in _AUXILIARY_SCHEMAS.items():
        try:
            payload = (_RESOURCE_ROOT / relative).read_bytes()
        except OSError as exc:
            raise _unavailable() from exc
        if hashlib.sha256(payload).hexdigest() != expected:
            raise _unavailable()
        originals[relative] = payload
    return originals


def _load_modules(originals: Mapping[str, bytes]) -> ModuleType:
    """Private aliases only; normal imported modules and builtins are untouched."""
    with _LOCK:
        if _MODULES:
            return _MODULES[_SELECTION]

        def retained_import(
            name: str,
            globals: dict[str, Any] | None = None,
            locals: dict[str, Any] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> Any:
            private_imports = {
                _PREFIX + "research_graph_binding": _BINDING,
                "packages.contracts_py.decision_hub_contracts.context_selection_v3_contract_base": (
                    _CONTRACT_BASE
                ),
                (
                    "packages.contracts_py.decision_hub_contracts.generated."
                    "research_context_selection_v3"
                ): _GENERATED,
            }
            if level == 0 and name in private_imports:
                if not fromlist:
                    raise _unavailable()
                return _MODULES[private_imports[name]]
            return builtins.__import__(name, globals, locals, fromlist, level)

        added: list[str] = []
        try:
            # Original contracts load before their original consumers. Shared
            # execution/generation_proof keeps its strictly descending stack.
            for relative in _PRIVATE_SOURCE_FILES:
                alias = "_decision_hub_context_" + _VERSION + "_" + Path(relative).stem
                if alias in sys.modules:
                    raise _unavailable()
                module = ModuleType(alias)
                module.__file__ = str(_RESOURCE_ROOT / relative)
                module.__package__ = Path(relative).parent.as_posix().replace("/", ".")
                module.__dict__["__builtins__"] = {
                    **vars(builtins),
                    "__import__": retained_import,
                }
                sys.modules[alias] = module
                added.append(alias)
                _MODULES[relative] = module
                exec(compile(originals[relative], module.__file__, "exec"), module.__dict__)
        except BaseException:
            for alias in added:
                sys.modules.pop(alias, None)
            _MODULES.clear()
            raise
        return _MODULES[_SELECTION]


@contextmanager
def _read_only(session: Session) -> Generator[None]:
    if session.new or session.dirty or session.deleted:
        raise ValueError("context_selection_historical_dirty_session")
    connection = session.connection()

    def require_select(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if re.match(r"\s*SELECT\b", statement, flags=re.IGNORECASE) is None:
            raise ValueError("context_selection_historical_write_forbidden")

    event.listen(connection, "before_cursor_execute", require_select)
    try:
        with session.no_autoflush:
            yield
    finally:
        event.remove(connection, "before_cursor_execute", require_select)


def replay_retained_context_selection_in_session(
    session: Session,
    *,
    selector_identity: Mapping[str, object],
    run_id: str,
    reference_json: str,
    clock: Callable[[], datetime],
) -> str | None:
    """Authenticate/replay one registered original; None means unknown version.

    The caller must already have checked its raw row, locator and self-hash.
    This function still invokes the complete original reader independently.
    There is deliberately no current-permission or writer option.
    """
    if dict(selector_identity) != _IDENTITY:
        return None
    originals = _verified_resources()
    module = _load_modules(originals)
    reference = module.ContextSelectionReference.model_validate_json(reference_json)
    key = (id(session), run_id, reference.output_id)
    stack = _READ_STACK.get()
    if key in stack:
        raise ValueError("research_context_original_dependency_cycle")
    token = _READ_STACK.set((*stack, key))
    try:
        # __new__ avoids creating a Database/pool. The original read path does
        # not access database, sample a clock or execute a fence when inactive.
        service = module.ContextSelectionService.__new__(module.ContextSelectionService)
        service.clock = clock
        with _read_only(session):
            frozen = service.read_in_session(
                session,
                run_id=run_id,
                reference=reference,
                require_current_permission=False,
                expected_lease_owner=None,
            )
        return frozen.payload_json
    finally:
        _READ_STACK.reset(token)
