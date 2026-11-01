"""Capture a validated installed release, never a native or promotion witness.

This explicit bootstrap uses the product's existing read-only installation
guard. It does not accept a passed flag, candidate DTO or readiness response.
The ordinary worker path never calls this module's producer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from time import monotonic
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from apps.hub_worker.harness_baseline import HarnessBaselineWorkerConfig
from packages.kernel.decision_hub_kernel.application.product_release_provenance import (
    SOURCE_REF as PROVENANCE_SOURCE,
)
from packages.kernel.decision_hub_kernel.application.product_release_provenance import (
    resolve_product_receipt,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_harness_artifact import (
    HarnessInstalledRuntimeV1,
    InstalledHarnessBaseline,
    build_prepared_harness_artifact,
    capture_installed_harness_baseline,
    load_harness_artifact,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    normalized_relative_ref,
)

GUARD_PATH = "infra/dsh/validated-product-builds.ps1"
GUARD_SHA256 = "b7dda65ac008da102396e2436ec775e90b453c52105a015b804f07c3eca62d6a"
V2_GUARD_PATH = "infra/dsh/validated-product-builds-v2.ps1"
DISPATCH_PATH = "infra/dsh/product-build-validation.ps1"
PACK_PATH = "packs/crypto_macro_prepared/1.1.0"
ARCHIVE_REF = "research/harness/release-baseline"
RUNTIME_PATH = "packages/runtime_adapters/dsh_runtime/web_runtime.py"
RENDERER_PATH = "packages/kernel/decision_hub_kernel/application/research_policy_render_v2.py"
COMPILER_PATH = "packages/kernel/decision_hub_kernel/application/research_policy_compiler.py"
BOOTSTRAP_PATH = "apps/hub_worker/installed_harness_bootstrap.py"
DEPENDENCIES = (
    BOOTSTRAP_PATH,
    "apps/hub_worker/main.py",
    "apps/hub_worker/composition.py",
    "apps/hub_worker/harness_baseline.py",
    "apps/hub_worker/research.py",
    "packages/kernel/decision_hub_kernel/application/research_harness_artifact.py",
    "packages/kernel/decision_hub_kernel/application/research_harness_deployment.py",
    "packages/kernel/decision_hub_kernel/application/research_harness_consumption.py",
    "migrations/versions/0067_research_harness_baseline.py",
    "packages/kernel/decision_hub_kernel/application/research_binding.py",
    RUNTIME_PATH,
    RENDERER_PATH,
    COMPILER_PATH,
    "infra/dsh/upstream.lock.json",
)
MAX_RECEIPT_BYTES = 2_097_152


class InstalledHarnessBootstrapRequest(BaseModel):
    """Operator-selected immutable receipt identity, not a qualification grant."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    receipt_path: Annotated[StrictStr, Field(min_length=1, max_length=2048)]
    receipt_sha256: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]


@dataclass(frozen=True)
class InstalledHarnessBootstrap:
    baseline: InstalledHarnessBaseline
    config: HarnessBaselineWorkerConfig
    installation_receipt_sha256: str
    index_path: Path


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("harness_install_duplicate_json_key")
        result[key] = value
    return result


def _read(path: Path, *, limit: int) -> bytes:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_file():
        raise ValueError("harness_install_path_invalid")
    with resolved.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit or path.resolve(strict=True) != resolved:
        raise ValueError("harness_install_bytes_invalid")
    return raw


def _file(root: Path, relative: str) -> Path:
    normalized = normalized_relative_ref(relative)
    if normalized != relative:
        raise ValueError("harness_install_noncanonical_path")
    path = root / normalized
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("harness_install_path_outside_root")
    for entry in (path, *path.parents):
        if entry == root:
            break
        if entry.is_symlink() or entry.is_junction():
            raise ValueError("harness_install_linked_path")
    return path


def _guard_observation(status: str, started: float, *, returncode: int | None = None) -> None:
    """Safe startup observation, never permission/readiness or useful-work proof."""
    try:
        print(json.dumps(dict(
            schema_version="harness-install-guard-observation.v1",
            role="harness_install_guard", process_id=os.getpid(), status=status,
            elapsed_ms=round((monotonic() - started) * 1000, 3),
            returncode=returncode, authority="startup_observation_only",
        ), separators=(",", ":")), flush=True)
    except (OSError, ValueError):
        # Closed stdout during owned shutdown cannot replace the verifier result.
        pass


def _official_install_guard(root: Path, receipt: Path, resolved: dict[str, Any]) -> dict[str, str]:
    # Fixed code and environment transport: no caller command or shell quoting.
    guard = _file(root, GUARD_PATH)
    if _sha(_read(guard, limit=262_144)) != GUARD_SHA256:
        raise ValueError("harness_install_guard_source_changed")
    v2 = resolved["schema"] == "product-validated-builds.v2"
    pins = {GUARD_PATH: GUARD_SHA256}
    if v2:
        for relative in (DISPATCH_PATH, V2_GUARD_PATH, PROVENANCE_SOURCE):
            actual = _sha(_read(_file(root, relative), limit=MAX_RECEIPT_BYTES))
            if resolved["sources"].get(relative) != actual:
                raise ValueError("harness_install_verifier_source_unvalidated")
            pins[relative] = actual
        guard = _file(root, DISPATCH_PATH)
    env = os.environ.copy()
    env["DH_HARNESS_INSTALL_ROOT"] = str(root)
    env["DH_HARNESS_INSTALL_RECEIPT"] = str(receipt)
    env["DH_HARNESS_INSTALL_GUARD"] = str(guard)
    command = (
        "$ErrorActionPreference='Stop'; "
        ". $env:DH_HARNESS_INSTALL_GUARD; "
        + ("$proof=Test-ProductValidatedBuilds " if v2 else "$null=Test-ValidatedProductBuilds ")
        + "-RepositoryRoot $env:DH_HARNESS_INSTALL_ROOT "
        "-ReceiptPath $env:DH_HARNESS_INSTALL_RECEIPT"
    )
    if v2:
        command += "; $proof | ConvertTo-Json -Depth 10 -Compress"
    started = monotonic()
    _guard_observation("pending", started)
    try:
        done = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            # Startup installation authentication owns no research deadline.
            # Keep this same verifier alive until its real exit; the managed
            # launcher owns cancellation and its exact descendant tree.
            timeout=None,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("harness_install_guard_unavailable") from exc
    _guard_observation("process_returned", started, returncode=done.returncode)
    if done.returncode != 0:
        # Do not expose paths, arbitrary receipt evidence, or guard stderr.
        raise ValueError("harness_install_guard_rejected")
    if v2:
        try:
            proof = json.loads(done.stdout)
            if (
                proof["receipt_schema"] != resolved["schema"]
                or proof["receipt_sha256"] != _sha(_read(receipt, limit=MAX_RECEIPT_BYTES))
                or proof["verifier_source_sha256"] != pins[PROVENANCE_SOURCE]
            ):
                raise ValueError("harness_install_guard_proof_changed") from None
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("harness_install_guard_proof_changed") from exc
    for relative, expected in pins.items():
        if _sha(_read(_file(root, relative), limit=MAX_RECEIPT_BYTES)) != expected:
            raise ValueError("harness_install_guard_source_changed")
    _guard_observation("originals_verified", started, returncode=done.returncode)
    return pins


def _immutable_file(path: Path, raw: bytes) -> None:
    # Finish bytes before publishing their name. NTFS create-hard-link is
    # atomic and refuses an existing name: no reader sees a partial write and
    # no unknown existing archive is replaced. Unsupported filesystems fail.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".harness-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _read(path, limit=MAX_RECEIPT_BYTES) != raw:
                raise ValueError("harness_install_index_collision")
    finally:
        temporary.unlink(missing_ok=True)


def produce_installed_harness_baseline(
    request: InstalledHarnessBootstrapRequest,
    *,
    repository_root: Path,
    data_root: Path,
    refs: PreparedResearchPolicyRefs,
    allowed_capabilities: tuple[str, ...],
    execution_mode: Literal["live", "replay"],
    runtime_id: str,
) -> InstalledHarnessBootstrap:
    """Validate current installed bytes, archive release, then return owned inputs.

    A failed guard never creates an archive. Success proves installed files,
    not a loaded Host, a trusted principal, MethodV4 or paired qualification.
    No deployment pointer or database is read or written here.
    """
    request = InstalledHarnessBootstrapRequest.model_validate_json(request.model_dump_json())
    if runtime_id != "dsh" or execution_mode != "live":
        raise ValueError("harness_install_live_dsh_required")
    root = repository_root.resolve(strict=True)
    receipt_path = Path(request.receipt_path).resolve(strict=True)
    receipt_raw = _read(receipt_path, limit=MAX_RECEIPT_BYTES)
    if _sha(receipt_raw) != request.receipt_sha256:
        raise ValueError("harness_install_receipt_hash_mismatch")
    resolved = resolve_product_receipt(
        receipt_raw,
        expected_root=root,
        expected_sha256=request.receipt_sha256,
        read_resource=lambda relative: _read(_file(root, relative), limit=MAX_RECEIPT_BYTES),
    )
    receipt = dict(resolved.value)
    repository_value = receipt.get("repository_root")
    if (
        not isinstance(repository_value, str)
        or not Path(repository_value).is_absolute()
        or Path(repository_value).resolve(strict=True) != root
    ):
        raise ValueError("harness_install_repository_mismatch")
    sources = receipt.get("sources")
    if (
        not isinstance(sources, dict)
        or not sources
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for key, value in sources.items()
        )
    ):
        raise ValueError("harness_install_source_map_invalid")
    plugin = receipt.get("plugin_build_hash")
    if not isinstance(plugin, str) or re.fullmatch(r"[a-f0-9]{64}", plugin) is None:
        raise ValueError("harness_install_plugin_identity_invalid")
    verifier_sources = _official_install_guard(root, receipt_path, receipt)
    # Require the complete actual Pack inventory plus the consumption chain.
    pack_root = (root / PACK_PATH).resolve(strict=True)
    if not pack_root.is_relative_to(root):
        raise ValueError("harness_install_pack_outside_root")
    pack_files = tuple(
        path.relative_to(root).as_posix() for path in sorted(pack_root.rglob("*")) if path.is_file()
    )
    required = (*DEPENDENCIES, *pack_files)
    before: dict[str, str] = {}
    for relative in required:
        actual = _sha(_read(_file(root, relative), limit=MAX_RECEIPT_BYTES))
        if sources.get(relative) != actual:
            raise ValueError("harness_install_baseline_source_unvalidated")
        before[relative] = actual
    if before[BOOTSTRAP_PATH] != _sha(Path(__file__).read_bytes()):
        raise ValueError("harness_install_loaded_bootstrap_source_mismatch")
    lock = json.loads(_read(_file(root, "infra/dsh/upstream.lock.json"), limit=65_536))
    if (
        not isinstance(lock, dict)
        or lock.get("schema_version") != "decision-hub.dsh-upstream-lock.v1"
    ):
        raise ValueError("harness_install_upstream_lock_invalid")
    version = lock.get("source_version")
    if not isinstance(version, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", version) is None:
        raise ValueError("harness_install_upstream_version_invalid")
    runtime = HarnessInstalledRuntimeV1(
        runtime_id=runtime_id,
        runtime_version=f"installed-source-{version}",
        plugin_build_hash=plugin,
        runtime_source_hash=before[RUNTIME_PATH],
        renderer_source_hash=before[RENDERER_PATH],
        compiler_source_hash=before[COMPILER_PATH],
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
    )
    baseline = capture_installed_harness_baseline(
        pack_root,
        refs,
        allowed_capabilities=allowed_capabilities,
        execution_mode=execution_mode,
        runtime=runtime,
    )
    artifact = build_prepared_harness_artifact(
        pack_root,
        refs,
        candidate_id="release",
        version=f"release.{plugin[:20]}",
        allowed_capabilities=allowed_capabilities,
        baseline=baseline,
    )
    after_pack_files = tuple(
        path.relative_to(root).as_posix() for path in sorted(pack_root.rglob("*")) if path.is_file()
    )
    if (
        pack_files != after_pack_files
        or _read(receipt_path, limit=MAX_RECEIPT_BYTES) != receipt_raw
    ):
        raise ValueError("harness_install_snapshot_changed")
    for relative, expected in before.items():
        if _sha(_read(_file(root, relative), limit=MAX_RECEIPT_BYTES)) != expected:
            raise ValueError("harness_install_snapshot_changed")
    final = resolve_product_receipt(
        receipt_raw,
        expected_root=root,
        expected_sha256=request.receipt_sha256,
        read_resource=lambda relative: _read(_file(root, relative), limit=MAX_RECEIPT_BYTES),
    )
    if final.value != resolved.value or final.transport != resolved.transport:
        raise ValueError("harness_install_snapshot_changed")
    for relative, expected in verifier_sources.items():
        if _sha(_read(_file(root, relative), limit=MAX_RECEIPT_BYTES)) != expected:
            raise ValueError("harness_install_snapshot_changed")
    data = data_root.resolve(strict=True)
    archive = data
    for part in ARCHIVE_REF.split("/"):
        archive = archive / part
        archive.mkdir(exist_ok=True)
        if (
            archive.is_symlink()
            or archive.is_junction()
            or not archive.resolve().is_relative_to(data)
        ):
            raise ValueError("harness_install_archive_outside_data_root")
    _immutable_file(archive / artifact.index.content_address, artifact.raw_bytes)
    load_harness_artifact(archive, artifact.index, baseline=baseline)
    index = {
        "schema_version": "installed-harness-release-index.v1",
        "installation_receipt_sha256": request.receipt_sha256,
        "installation_guard_sha256": GUARD_SHA256,
        "artifact_index": artifact.index.model_dump(mode="json"),
        "baseline_definition_json": baseline.definition_json.decode("utf-8"),
        "baseline_runtime_json": baseline.runtime_json.decode("utf-8"),
        "validated_sources": before,
        "native_execution_proof": "unverified",
        "eligible_for_deployment": False,
    }
    if receipt["schema"] == "product-validated-builds.v2":
        index.update(
            {
                "schema_version": "installed-harness-release-index.v2",
                "installation_receipt_schema": receipt["schema"],
                "installation_verifier_sources": verifier_sources,
                "installation_receipt_resources": dict(resolved.transport),
            }
        )
    raw_index = canonical_recipe(index).encode("utf-8")
    # Installation-specific provenance gets its own address. Artifact dedup
    # remains by actual bytes; a second valid install never overwrites history.
    index_path = archive / f"{_sha(raw_index)}.index.json"
    _immutable_file(index_path, raw_index)
    config = HarnessBaselineWorkerConfig(
        schema_version="research-harness-worker-baseline-config.v1",
        selection_mode="release_baseline_only",
        archive_relative_ref=ARCHIVE_REF,
        release_artifact_index=artifact.index,
    )
    return InstalledHarnessBootstrap(baseline, config, request.receipt_sha256, index_path)
