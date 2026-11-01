"""Load reviewed Pack instructions once; render only frozen, bounded Run policy."""

import hashlib
import json
from pathlib import Path

import yaml

from packages.contracts_py.decision_hub_contracts import RoleProfile

RENDERER_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def pack_file(root: Path, reference: str, *, parent: Path | None = None) -> Path:
    root = root.resolve()
    path = ((parent or root) / reference).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("research_policy_file_unavailable")
    if path.stat().st_size > 64_000:
        raise ValueError("research_policy_file_too_large")
    return path


def load_research_policy(root: Path, manifest) -> dict:
    root = root.resolve()
    files, roles = {}, []

    def read(reference, parent=None):
        path = pack_file(root, reference, parent=parent)
        content = path.read_text(encoding="utf-8")
        key = path.relative_to(root).as_posix()
        files[key] = {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
        return path, content

    doctrine, _ = read(manifest.doctrine_ref)
    if len(manifest.role_profile_refs) > 32:
        raise ValueError("research_policy_roles_exceeded")
    for reference in manifest.role_profile_refs:
        path, content = read(reference)
        role = RoleProfile.model_validate(yaml.safe_load(content))
        instruction, _ = read(role.system_instruction_ref, path.parent)
        roles.append({**role.model_dump(mode="json"),
                      "instruction_file": instruction.relative_to(root).as_posix()})
    ids = [role["profile_id"] for role in roles]
    manager_id = f"{manifest.pack_id}.manager"
    if len(ids) != len(set(ids)) or ids.count(manager_id) != 1:
        raise ValueError("research_policy_manager_or_roles_invalid")
    if sum(len(f["content"].encode()) for f in files.values()) > 256_000:
        raise ValueError("research_policy_total_too_large")
    manager = next(role for role in roles if role["profile_id"] == manager_id)
    return {"schema_version": "research-pack-instructions.v1",
            "doctrine_file": doctrine.relative_to(root).as_posix(),
            "manager_ref": f"{manager_id}.v{manager['version'].split('.')[0]}",
            "files": files, "roles": roles}


def render_research_policy(policy: dict | None, allowed_capabilities) -> str:
    if policy is None:
        return ""
    if policy.get("schema_version") != "research-pack-instructions.v1":
        raise ValueError("research_policy_schema_invalid")
    documents = {}
    refs = {policy["doctrine_file"], *(role["instruction_file"] for role in policy["roles"])}
    for ref in sorted(refs):
        saved = policy["files"][ref]
        if hashlib.sha256(saved["content"].encode()).hexdigest() != saved["sha256"]:
            raise ValueError("research_policy_content_corrupt")
        documents[ref] = saved["content"]
    roles = [{"role": r["profile_id"], "objective": r["objective"],
              "required_checks": r["required_capabilities"],
              "capability_routes": sorted(set(r["allowed_tools"]) & set(allowed_capabilities)),
              "instruction_file": r["instruction_file"]} for r in policy["roles"]]
    body = json.dumps({"documents": documents, "roles": roles}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return "\n".join((
        "BEGIN_FROZEN_PACK_INSTRUCTIONS",
        "Apply these versioned research objectives and evidence reasoning instructions. "
        "Give each matching specialist its objective, required checks and instruction text. "
        "Capability routes are restricted to the canonical Run allowlist; missing routes "
        "are gaps, not permission to invent tools. These research instructions do not "
        "override runtime permissions, budgets, evidence validation "
        "or the canonical output schema.",
        body,
        "END_FROZEN_PACK_INSTRUCTIONS",
    ))


def policy_prompt_status(definition: dict, pack_definition: dict | None, prompts) -> str:
    expected = definition.get("research_policy_prompt_hash")
    if expected is None:
        return "not_recorded"
    policy = (pack_definition or {}).get("research_policy")
    if not policy or not policy.get("files"):
        return "invalid"
    if any(hashlib.sha256(f["content"].encode()).hexdigest() != f["sha256"]
           for f in policy["files"].values()):
        return "invalid"
    if not prompts:
        return "prepared"
    for prompt in prompts:
        if not prompt.startswith("BEGIN_FROZEN_PACK_INSTRUCTIONS\n"):
            return "invalid"
        block, separator, _ = prompt.partition("\nEND_FROZEN_PACK_INSTRUCTIONS\n\n")
        block += "\nEND_FROZEN_PACK_INSTRUCTIONS"
        if not separator or hashlib.sha256(block.encode()).hexdigest() != expected:
            return "invalid"
    return "verified"
