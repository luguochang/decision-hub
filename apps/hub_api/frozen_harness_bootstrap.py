"""Trusted launch configuration for API, existing Worker and research MCP.

No keys are read from user requests/Host identity, no DB is opened here, and
constructing this dependency never migrates, seeds or changes a pointer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from apps.hub_api.local_owner_session import LocalOwnerSessionAuthority
from packages.contracts_py.decision_hub_contracts import ExecutionBudget
from packages.contracts_py.decision_hub_contracts.generated.harness_comparison import (
    HarnessFrozenRunCaseV1,
)
from packages.kernel.decision_hub_kernel.application.product_release_provenance import (
    SOURCE_REF,
    read_repository_file,
    resolve_product_receipt,
)
from packages.kernel.decision_hub_kernel.application.research_harness_artifact import (
    HarnessArtifactIndexV1,
    InstalledHarnessBaseline,
    validate_harness_artifact_bytes,
)
from packages.kernel.decision_hub_kernel.application.research_harness_frozen_run import (
    FrozenCatalog,
    FrozenRunHarnessService,
    case_rows,
    sha,
)


class PrivateFrozenRunConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    schema_version: Literal['harness-frozen-run-private-launch.v1']
    owner_identity: Annotated[StrictStr,Field(min_length=1,max_length=128)]
    origin: Annotated[StrictStr,Field(min_length=1,max_length=2048)]
    signing_key_hex: Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$',repr=False)]
    pairing_sha256: Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$',repr=False)]
    case_json: Annotated[StrictStr,Field(min_length=1,max_length=8_000_000,repr=False)]
    baseline_definition_json: Annotated[StrictStr,Field(min_length=1,max_length=1_000_000)]
    baseline_runtime_json: Annotated[StrictStr,Field(min_length=1,max_length=100_000)]
    baseline_artifact_json: Annotated[StrictStr,Field(min_length=1,max_length=1_000_000)]
    candidate_artifact_json: Annotated[StrictStr,Field(min_length=1,max_length=1_000_000)]
    baseline_index: HarnessArtifactIndexV1
    candidate_index: HarnessArtifactIndexV1
    candidate_ref: Annotated[StrictStr,Field(min_length=1,max_length=128)]
    candidate_label: Annotated[StrictStr,Field(min_length=1,max_length=256)]
    budget_json: Annotated[StrictStr,Field(min_length=1,max_length=10000)]
    data_root: Annotated[StrictStr,Field(min_length=1,max_length=2048)]
    current_release_index_path: Annotated[StrictStr,Field(min_length=1,max_length=2048)]
    current_release_index_sha256: Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$')]
    installation_receipt_path: Annotated[StrictStr,Field(min_length=1,max_length=2048)]
    installation_receipt_sha256: Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$')]
    prepared_only: bool

CURRENT_CORE_SOURCES=(
    'apps/hub_api/frozen_harness_bootstrap.py','apps/hub_api/local_owner_session.py',
    'apps/hub_api/main.py','packages/query_views/research/service.py',
    'apps/hub_api/harness_comparison_routes.py','apps/hub_worker/composition.py','apps/hub_worker/research.py',
    'apps/research_mcp/main.py','packages/workbench_adapters/durable_research_gateway.py',
    'packages/workbench_adapters/frozen_run_research_source.py',
    'packages/kernel/decision_hub_kernel/application/research_harness_frozen_run.py',
    'packages/kernel/decision_hub_kernel/application/research_harness_execution.py',
    'packages/contracts_py/decision_hub_contracts/harness_comparison_contract_base.py',
    'packages/contracts_py/decision_hub_contracts/generated/harness_comparison.py',
)


def verify_current_installation(config: PrivateFrozenRunConfig) -> None:
    """Read the actual producer's archive and validated installation receipt.

    API and MCP enforce the same current source/runtime gate as the worker;
    an operator's prepared-only private file never becomes startup authority.
    """
    if config.prepared_only:
        raise ValueError("harness_frozen_prepared_config_not_installed")
    root = next(
        p for p in Path(__file__).resolve().parents if (p / "pnpm-workspace.yaml").is_file()
    )

    def bound_bytes(value, digest, limit=2_097_152):
        path = Path(value)
        resolved = path.resolve(strict=True)
        if not path.is_absolute() or not resolved.is_relative_to(root) or path.is_symlink():
            raise ValueError("harness_frozen_current_installation_path_invalid")
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        if len(content) > limit or sha(content) != digest or path.resolve(strict=True) != resolved:
            raise ValueError("harness_frozen_current_installation_changed")
        return content

    installed = json.loads(
        bound_bytes(config.current_release_index_path, config.current_release_index_sha256)
    )
    resolved_receipt = resolve_product_receipt(
        bound_bytes(config.installation_receipt_path, config.installation_receipt_sha256),
        expected_root=root,
        expected_sha256=config.installation_receipt_sha256,
        read_resource=lambda ref: read_repository_file(root, ref),
    )
    receipt = resolved_receipt.value
    schema = installed.get("schema_version")
    if (
        schema not in {"installed-harness-release-index.v1", "installed-harness-release-index.v2"}
        or installed["installation_receipt_sha256"] != config.installation_receipt_sha256
        or installed["baseline_definition_json"] != config.baseline_definition_json
        or installed["baseline_runtime_json"] != config.baseline_runtime_json
        or installed["artifact_index"] != config.baseline_index.model_dump(mode="json")
    ):
        raise ValueError("harness_frozen_current_installation_changed")
    if schema == "installed-harness-release-index.v1":
        if receipt["schema"] != "product-validated-builds.v1":
            raise ValueError("harness_frozen_current_installation_changed")
    else:
        required = {
            "infra/dsh/validated-product-builds.ps1",
            "infra/dsh/validated-product-builds-v2.ps1",
            "infra/dsh/product-build-validation.ps1",
            SOURCE_REF,
        }
        pins = installed.get("installation_verifier_sources")
        if (
            receipt["schema"] != "product-validated-builds.v2"
            or installed.get("installation_receipt_schema") != receipt["schema"]
            or installed.get("installation_receipt_resources") != dict(resolved_receipt.transport)
            or type(pins) is not dict
            or set(pins) != required
            or pins["infra/dsh/validated-product-builds.ps1"]
            != "b7dda65ac008da102396e2436ec775e90b453c52105a015b804f07c3eca62d6a"
        ):
            raise ValueError("harness_frozen_current_installation_changed")
        for ref, digest in pins.items():
            if sha(read_repository_file(root, ref)) != digest or (
                ref != "infra/dsh/validated-product-builds.ps1"
                and receipt["sources"].get(ref) != digest
            ):
                raise ValueError("harness_frozen_current_verifier_changed")
    expected = dict(installed["validated_sources"])
    for ref in CURRENT_CORE_SOURCES:
        digest = receipt.get("sources", {}).get(ref)
        if not isinstance(digest, str) or re.fullmatch("[a-f0-9]{64}", digest) is None:
            raise ValueError("harness_frozen_current_source_unvalidated")
        if ref in expected and expected[ref] != digest:
            raise ValueError("harness_frozen_current_source_unvalidated")
        expected[ref] = digest
    for ref, digest in expected.items():
        path = root / ref
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or path.is_symlink():
            raise ValueError("harness_frozen_current_source_changed")
        with path.open("rb") as stream:
            content = stream.read(2_097_153)
        if len(content) > 2_097_152 or sha(content) != digest:
            raise ValueError("harness_frozen_current_source_changed")
    final = resolve_product_receipt(
        bound_bytes(config.installation_receipt_path, config.installation_receipt_sha256),
        expected_root=root,
        expected_sha256=config.installation_receipt_sha256,
        read_resource=lambda ref: read_repository_file(root, ref),
    )
    if (
        final.value != resolved_receipt.value
        or final.transport != resolved_receipt.transport
        or json.loads(
            bound_bytes(config.current_release_index_path, config.current_release_index_sha256)
        )
        != installed
    ):
        raise ValueError("harness_frozen_current_installation_changed")


def verify_raw_files(case: HarnessFrozenRunCaseV1,data_root: Path) -> None:
    """Bounded streaming validation; this authenticates bytes, not rights."""
    root=(data_root/'raw-captures').resolve(strict=True)
    total=0
    deadline=time.monotonic()+5
    captures=case_rows(case,'raw_captures')
    if not captures or len(captures)>15:raise ValueError('harness_frozen_capture_set_invalid')
    for item in captures:
        if time.monotonic()>deadline:raise ValueError('harness_frozen_capture_deadline_exceeded')
        ref=item['body_ref'];digest=item['body_hash']
        if not isinstance(ref,str) or re.fullmatch(r'raw://sha256/[a-f0-9]{64}',ref) is None or ref.rsplit('/',1)[1]!=digest:
            raise ValueError('harness_frozen_capture_bytes_unavailable')
        path=root/digest[:2]/digest;resolved=path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file() or path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():raise ValueError('harness_frozen_capture_path_invalid')
        before=path.stat();actual=hashlib.sha256();count=0
        with path.open('rb') as stream:
            handle_before=os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(handle_before.st_dev,handle_before.st_ino,handle_before.st_size,handle_before.st_mtime_ns):raise ValueError('harness_frozen_capture_changed')
            while chunk:=stream.read(65536):
                count+=len(chunk);total+=len(chunk)
                if count>2*1024*1024 or total>32*1024*1024:raise ValueError('harness_frozen_capture_size_exceeded')
                if time.monotonic()>deadline:raise ValueError('harness_frozen_capture_deadline_exceeded')
                actual.update(chunk)
            handle_after=os.fstat(stream.fileno())
        after=path.stat()
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or (handle_before.st_dev,handle_before.st_ino,handle_before.st_size,handle_before.st_mtime_ns,handle_before.st_ctime_ns)!=(handle_after.st_dev,handle_after.st_ino,handle_after.st_size,handle_after.st_mtime_ns,handle_after.st_ctime_ns) or count!=item['byte_count'] or actual.hexdigest()!=digest or path.resolve(strict=True)!=resolved:
            raise ValueError('harness_frozen_capture_changed')

@dataclass(frozen=True,repr=False)
class FrozenRunBootstrap:
    service: FrozenRunHarnessService
    authority: LocalOwnerSessionAuthority
    config_sha256: str

def configured_frozen_run_harness(database,*,installed_baseline=None) -> FrozenRunBootstrap | None:
    path_value=os.getenv('DECISION_HUB_HARNESS_COMPARE_CONFIG','').strip()
    expected=os.getenv('DECISION_HUB_HARNESS_COMPARE_SHA256','').strip()
    if not path_value and not expected:return None
    if not path_value or re.fullmatch(r'[a-f0-9]{64}',expected) is None:raise ValueError('harness_frozen_private_config_required')
    path=Path(path_value);resolved=path.resolve(strict=True)
    if not path.is_absolute() or path.is_symlink() or not resolved.is_file():raise ValueError('harness_frozen_private_config_path_invalid')
    with path.open('rb') as stream:content=stream.read(12_000_001)
    if len(content)>12_000_000 or sha(content)!=expected or path.resolve(strict=True)!=resolved:raise ValueError('harness_frozen_private_config_changed')
    config=PrivateFrozenRunConfig.model_validate_json(content)
    verify_current_installation(config)
    baseline=InstalledHarnessBaseline(config.baseline_definition_json.encode(),config.baseline_runtime_json.encode())
    if installed_baseline is not None and (installed_baseline.definition!=baseline.definition or installed_baseline.runtime!=baseline.runtime):raise ValueError('harness_frozen_current_installation_changed')
    baseline_artifact=validate_harness_artifact_bytes(config.baseline_artifact_json.encode(),config.baseline_index,baseline=baseline)
    candidate_artifact=validate_harness_artifact_bytes(config.candidate_artifact_json.encode(),config.candidate_index,baseline=baseline)
    data=Path(config.data_root).resolve(strict=True)
    actual=Path(os.getenv('DECISION_HUB_DATA_DIR','data/decision-hub')).resolve(strict=True)
    if data!=actual:raise ValueError('harness_frozen_data_root_changed')
    authority=LocalOwnerSessionAuthority(signing_key=bytes.fromhex(config.signing_key_hex),pairing_sha256=bytes.fromhex(config.pairing_sha256),owner_identity=config.owner_identity,
        domain_pack_ref=f'{baseline.definition.manifest.pack_id}.v{baseline.definition.manifest.version.split(".")[0]}',origin=config.origin)
    catalog=FrozenCatalog(config.case_json,baseline,baseline_artifact,candidate_artifact,config.candidate_ref,config.candidate_label,config.budget_json)
    service=FrozenRunHarnessService(database,catalog=catalog,owner_identity=config.owner_identity,authorizer=authority,signing_key=bytes.fromhex(config.signing_key_hex),raw_verifier=lambda case:verify_raw_files(case,data))
    return FrozenRunBootstrap(service,authority,expected)
