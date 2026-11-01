"""Actual frozen Factory inputs and pure V2 instruction projection boundaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator, Iterator
from dataclasses import replace
from itertools import repeat
from pathlib import Path
from typing import Any

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchCompiledPolicyReceiptV1,
    ResearchTaskReceiptV2,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyResource,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    BEGIN_MARKER,
    END_MARKER,
    RENDERER_HASH,
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    canonical_hash,
    resource_definition,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)

# Register the existing post-import socket fixture explicitly; it permits only
# the Windows stdlib socketpair's synchronous, same-thread loopback connection.
pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


@pytest.fixture
def original_bundle(tmp_path: Path) -> Generator[tuple[Database, FrozenResearchBundle, Path]]:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        factory = ResearchRequestFactory(
            database, pack_root=root, execution_mode="replay",
            allowed_capabilities=("replay.research", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        )
        factory.build(event_id, run_id)
        with database.session() as session:
            frozen = read_research_bundle_in_session(session, run_id)
        assert isinstance(frozen, FrozenResearchBundle)
        yield database, frozen, root
    finally:
        database.engine.dispose()


def _rows(database: Database, run_id: str) -> list[dict[str, Any]]:
    with database.session() as session:
        return [
            {"output_id": row.output_id, "role": row.role, "schema": row.schema_version,
             "payload": row.payload_json, "hash": row.payload_hash,
             "created_at": row.created_at.isoformat()}
            for row in session.query(RunRoleOutputRecord).filter_by(run_id=run_id)
            .order_by(RunRoleOutputRecord.role).all()
        ]


def _projection(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    assert lines[0] == BEGIN_MARKER and lines[-1] == END_MARKER
    return json.loads(lines[2])


def _observe(tmp_path: Path, **body: Any) -> None:
    (tmp_path / "observation.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def test_frozen_original_text_declarations_and_unavailable_checks_survive_projection(
    original_bundle: tuple[Database, FrozenResearchBundle, Path], tmp_path: Path,
) -> None:
    database, bundle, _root = original_bundle
    before = _rows(database, bundle.task.run_id)
    text = render_prepared_research_policy(bundle, bundle.request.allowed_capabilities)
    body = _projection(text)
    _observe(tmp_path, text=text, projection=body, original_rows=before,
             after_rows=_rows(database, bundle.task.run_id), renderer_hash=RENDERER_HASH)
    assert body["documents"] == {
        key: saved.content for key, saved in bundle.pack.definition.research_policy.files.items()
    }
    assert body["policy_declarations"]["gate"] == resource_definition(
        ResearchPolicyResource.model_validate(
            bundle.pack.definition.gate_policy_resource.model_dump(mode="json"),
        ),
    )
    assert body["policy_declarations"]["rubric"] == resource_definition(
        ResearchPolicyResource.model_validate(
            bundle.pack.definition.rubric_policy_resource.model_dump(mode="json"),
        ),
    )
    prepared = body["prepared_receipt"]
    assert prepared["status"] == prepared["checker_activation"] == "prepared"
    assert prepared["allowed_execution"] is False
    assert prepared["supported_rule_ids"] == []
    assert len(prepared["unavailable_rule_ids"]) == len(prepared["rules"]) == 14
    assert all(rule["status"] == "not_available" for rule in prepared["rules"])
    assert _rows(database, bundle.task.run_id) == before


def test_disk_changes_cannot_replace_saved_original_instructions(
    original_bundle: tuple[Database, FrozenResearchBundle, Path], tmp_path: Path,
) -> None:
    _database, bundle, root = original_bundle
    text = render_prepared_research_policy(bundle, bundle.request.allowed_capabilities)
    doctrine = root / bundle.pack.definition.research_policy.doctrine_file
    original_disk = hashlib.sha256(doctrine.read_bytes()).hexdigest()
    doctrine.write_bytes(b"A later local edit must not enter the original Run prompt.\r\n")
    rendered = render_prepared_research_policy(bundle, bundle.request.allowed_capabilities)
    _observe(tmp_path, original_disk_sha=original_disk,
             edited_disk_sha=hashlib.sha256(doctrine.read_bytes()).hexdigest(),
             original_prompt_sha=hashlib.sha256(text.encode()).hexdigest(),
             repeated_prompt_sha=hashlib.sha256(rendered.encode()).hexdigest())
    assert text == rendered
    assert "A later local edit" not in rendered


def test_current_routes_only_narrow_the_original_allowlist(
    original_bundle: tuple[Database, FrozenResearchBundle, Path], tmp_path: Path,
) -> None:
    _database, bundle, _root = original_bundle
    capabilities = bundle.request.allowed_capabilities
    text = render_prepared_research_policy(bundle, capabilities)
    reordered = render_prepared_research_policy(bundle, list(reversed(capabilities)))
    narrowed = _projection(render_prepared_research_policy(bundle, ["web.search"]))
    closed = _projection(render_prepared_research_policy(bundle, []))
    _observe(tmp_path, original=_projection(text), narrowed=narrowed, closed=closed)
    assert text == reordered
    assert all(set(role["capability_routes"]) <= {"web.search"} for role in narrowed["roles"])
    assert all(role["capability_routes"] == [] for role in closed["roles"])
    assert narrowed["prepared_receipt"] == closed["prepared_receipt"]


def test_a_mutating_sequence_cannot_replace_checked_routes_during_projection(
    original_bundle: tuple[Database, FrozenResearchBundle, Path], tmp_path: Path,
) -> None:
    class DriftingList(list[str]):
        iterations = 0

        def __iter__(self) -> Iterator[str]:
            self.iterations += 1
            return iter(["web.search" if self.iterations <= 3 else "web.fetch"])

    _database, bundle, _root = original_bundle
    assert "web.fetch" not in bundle.request.allowed_capabilities
    assert any("web.fetch" in role.profile.allowed_tools
               for role in bundle.pack.definition.research_policy.roles)
    capabilities = DriftingList(["web.search"])
    body = _projection(render_prepared_research_policy(bundle, capabilities))
    _observe(tmp_path, iterator_calls=capabilities.iterations, projection=body,
             original_capabilities=bundle.request.allowed_capabilities)
    assert all(set(role["capability_routes"]) <= {"web.search"} for role in body["roles"])


def test_an_unbounded_sequence_is_rejected_without_consuming_it_all(
    original_bundle: tuple[Database, FrozenResearchBundle, Path],
) -> None:
    class EndlessList(list[str]):
        def __iter__(self) -> Iterator[str]:
            return repeat("web.search")

    _database, bundle, _root = original_bundle
    with pytest.raises(ValueError, match="research_policy_render_capabilities_invalid"):
        render_prepared_research_policy(bundle, EndlessList(["web.search"]))


@pytest.mark.parametrize("capabilities", [["undeclared.tool"], ["web.search", "web.search"],
                                         [True], "web.search"])
def test_invalid_current_capabilities_cannot_create_a_route(
    original_bundle: tuple[Database, FrozenResearchBundle, Path], capabilities: Any,
) -> None:
    _database, bundle, _root = original_bundle
    with pytest.raises(ValueError, match="research_policy_render_capabilities_invalid"):
        render_prepared_research_policy(bundle, capabilities)


@pytest.mark.parametrize("resign_file", [False, True])
def test_mutable_or_resigned_document_is_rejected_by_original_bundle_hash(
    original_bundle: tuple[Database, FrozenResearchBundle, Path], resign_file: bool,
) -> None:
    _database, bundle, _root = original_bundle
    changed = bundle.pack.model_copy(deep=True)
    saved = changed.definition.research_policy.files[
        changed.definition.research_policy.doctrine_file
    ]
    saved.content += "\nUNBOUND LATER INSTRUCTION"
    if resign_file:
        saved.sha256 = hashlib.sha256(saved.content.encode()).hexdigest()
    with pytest.raises(ValueError, match="research_bundle_binding_invalid"):
        render_prepared_research_policy(replace(bundle, pack=changed), [])


def test_repaired_compiled_hash_cannot_substitute_another_gate(
    original_bundle: tuple[Database, FrozenResearchBundle, Path],
) -> None:
    _database, bundle, _root = original_bundle
    changed = bundle.compiled.model_dump(mode="json")
    gate = changed["compiled"]["gate_resource"]
    gate["raw_utf8"] += "\n# LATER GATE RAW BYTES\n"
    gate["raw_sha256"] = hashlib.sha256(gate["raw_utf8"].encode()).hexdigest()
    compiled_body = {key: value for key, value in changed["compiled"].items()
                     if key != "compiled_hash"}
    changed["compiled"]["compiled_hash"] = canonical_hash(compiled_body)
    task = bundle.task.model_dump(mode="json")
    task["compiled_policy_hash"] = changed["compiled"]["compiled_hash"]
    modified = replace(
        bundle, compiled=ResearchCompiledPolicyReceiptV1.model_validate(changed),
        task=ResearchTaskReceiptV2.model_validate(task),
    )
    with pytest.raises(ValueError, match="research_bundle_prepared_policy_invalid"):
        render_prepared_research_policy(modified, [])


def test_escape_expansion_is_bounded_even_when_original_resource_bytes_are_valid(
    tmp_path: Path,
) -> None:
    # The actual default Pack shares one instruction file, so expanding all
    # its frozen documents would hit only 64 KiB. Create distinct real files
    # and legal role references before Factory loading, without resigning rows.
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        (root / manifest["doctrine_ref"]).write_bytes(b"\x00" * 65_536)
        for index, reference in enumerate(manifest["role_profile_refs"][:2]):
            role_file = root / reference
            role = yaml.safe_load(role_file.read_text(encoding="utf-8"))
            name = f"render-escaped-{index}.txt"
            (root / name).write_bytes(b"\x00" * 65_536)
            role["system_instruction_ref"] = f"../{name}"
            role_file.write_text(yaml.safe_dump(role), encoding="utf-8")
        factory = ResearchRequestFactory(
            database, pack_root=root, execution_mode="replay",
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        )
        factory.build(event_id, run_id)
        with database.session() as session:
            bundle = read_research_bundle_in_session(session, run_id)
        assert isinstance(bundle, FrozenResearchBundle)
        _observe(tmp_path, valid_original_rows=_rows(database, run_id),
                 document_bytes=sum(len(file.content.encode()) for file in
                                    bundle.pack.definition.research_policy.files.values()))
        with pytest.raises(ValueError, match="research_policy_render_block_too_large"):
            render_prepared_research_policy(bundle, [])
    finally:
        database.engine.dispose()
