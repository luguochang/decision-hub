from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSynthesisCandidate,
)

DEFAULT_PROFILE_PATH = Path(__file__).parent / "profiles" / "decision-research.cordis.yml"
REQUIRED_PLUGINS = {
    "@deepseek-ai/dsh-sdk-jsonrpc-server",
    "@deepseek-ai/dsh-llm-deepseek",
    "@deepseek-ai/dsh-agent-spine-demo",
    "@deepseek-ai/dsh-session-persistence-jsonl",
    "@deepseek-ai/dsh-session-checkpoint-policy",
    "@deepseek-ai/dsh-subagent",
    "@deepseek-ai/dsh-subagent-spawn-in-process",
    "@deepseek-ai/dsh-tool-subagent",
    "@deepseek-ai/dsh-tool-todo",
    "@deepseek-ai/dsh-mcp-client",
    "@deepseek-ai/dsh-compaction-basic",
}
UNATTENDED_REASONING_EFFORT = "low"
DENIED_PLUGIN_FRAGMENTS = (
    "bash",
    "terminal",
    "sandbox",
    "tool-fs",
    "fs-local",
    "plugin-market",
    "plugin-installer",
)


class _CordisLoader(yaml.SafeLoader):
    pass


def _construct_js(loader: _CordisLoader, node: yaml.ScalarNode) -> str:
    return loader.construct_scalar(node)


_CordisLoader.add_constructor("tag:yaml.org,2002:js", _construct_js)


@dataclass(frozen=True)
class DshProfileInspection:
    profile_id: str
    profile_version: str
    path: Path
    content_hash: str
    plugin_ids: tuple[str, ...]
    plugin_names: tuple[str, ...]
    reasoning_effort: str

    @property
    def profile_ref(self) -> str:
        return f"{self.profile_id}.{self.profile_version}:{self.content_hash[:16]}"


def inspect_profile(path: Path = DEFAULT_PROFILE_PATH) -> DshProfileInspection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError("dsh_profile_not_found")
    raw = resolved.read_bytes()
    document = yaml.load(raw.decode("utf-8"), Loader=_CordisLoader)
    if not isinstance(document, list) or not document:
        raise ValueError("dsh_profile_invalid")

    ids: list[str] = []
    names: list[str] = []
    reasoning_effort: str | None = None
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("dsh_profile_entry_invalid")
        plugin_id = item.get("id")
        plugin_name = item.get("name")
        if not isinstance(plugin_id, str) or not isinstance(plugin_name, str):
            raise ValueError("dsh_profile_plugin_identity_required")
        ids.append(plugin_id)
        names.append(plugin_name)
        if plugin_name == "@deepseek-ai/dsh-llm-deepseek":
            config = item.get("config")
            if not isinstance(config, dict):
                raise ValueError("dsh_profile_llm_config_required")
            configured_effort = config.get("reasoningEffort")
            if not isinstance(configured_effort, str):
                raise ValueError("dsh_profile_reasoning_effort_required")
            reasoning_effort = configured_effort

    if len(ids) != len(set(ids)):
        raise ValueError("dsh_profile_duplicate_plugin_id")
    missing = REQUIRED_PLUGINS - set(names)
    if missing:
        raise ValueError(f"dsh_profile_required_plugins_missing:{','.join(sorted(missing))}")
    denied = [
        name
        for name in names
        if any(fragment in name.lower() for fragment in DENIED_PLUGIN_FRAGMENTS)
    ]
    if denied:
        raise ValueError(f"dsh_profile_denied_plugins:{','.join(sorted(denied))}")
    if reasoning_effort != UNATTENDED_REASONING_EFFORT:
        raise ValueError(
            "dsh_profile_reasoning_effort_invalid:"
            f"expected_{UNATTENDED_REASONING_EFFORT}"
        )

    return DshProfileInspection(
        profile_id="decision-research",
        profile_version="v1",
        path=resolved,
        content_hash=hashlib.sha256(raw).hexdigest(),
        plugin_ids=tuple(ids),
        plugin_names=tuple(names),
        reasoning_effort=reasoning_effort,
    )


def build_research_prompt(request: ResearchSessionRequest, research_session_id: str) -> str:
    """Build the legacy SDK/MCP prompt whose raw tool still requires Session identity."""

    return _build_research_prompt(
        request,
        capability_tool_name="research_capability_execute",
        research_session_id=research_session_id,
    )


def build_trusted_web_research_prompt(request: ResearchSessionRequest) -> str:
    """Build the Web prompt; DSH injects Session identity outside model arguments."""

    return _build_research_prompt(
        request,
        capability_tool_name="decision_hub_research",
        research_session_id=None,
    )


def _build_research_prompt(
    request: ResearchSessionRequest,
    *,
    capability_tool_name: str,
    research_session_id: str | None,
) -> str:
    result_schema = json.dumps(
        ResearchSynthesisCandidate.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    identity_instruction = (
        f"Runtime session id: {research_session_id}"
        if research_session_id is not None
        else (
            "Runtime Session identity is injected by the DSH Tool execution context. "
            "Never include, infer or copy a research_session_id in any tool call."
        )
    )
    return "\n".join(
        (
            "Complete one bounded Decision Hub research session.",
            identity_instruction,
            "Use subagents only for declared role capabilities. "
            "Use only tools exposed by the profile.",
            "If hard evidence is unavailable, preserve explicit gaps and stop fail-closed.",
            "Return exactly one JSON object matching the canonical synthesis JSON Schema below; "
            "do not use markdown fences and do not add fields not declared by the schema.",
            "This synthesis object contains research semantics only. The adapter owns "
            "Session, Round, Tool, Evidence, Coverage, timestamps, counts and accounting. "
            "Do not emit those runtime-ledger fields.",
            "Every evidence_refs value in causal_case or horizons must be either an "
            "input evidence_id from the canonical request or an evidence_id copied "
            f"exactly from a successful {capability_tool_name} result in this "
            "same session. Never invent or rewrite an evidence id.",
            f"For replay.research, call {capability_tool_name} with target_url=null, "
            "allowed_domains=[], and query set to the exact archived alias "
            "<event_id>:<requirement_id> from the request. Do not add domain filters.",
            *_capability_playbook(
                request,
                capability_tool_name=capability_tool_name,
                research_session_id=research_session_id,
            ),
            "Do not call todo_write in this bounded unattended session. After the required "
            "research tools finish, immediately return the canonical synthesis JSON.",
            "The product runtime will independently verify request/session identity, "
            "tool counts and trace.",
            "Canonical research-synthesis-candidate.v1 JSON Schema "
            "(generated from the product contract; this is the only model output shape):",
            result_schema,
            "Canonical request:",
            request.model_dump_json(exclude_none=False),
        )
    )


def _capability_playbook(
    request: ResearchSessionRequest,
    *,
    capability_tool_name: str,
    research_session_id: str | None,
) -> tuple[str, ...]:
    """Render provider-neutral, audited invocation hints for enabled Pack capabilities."""

    enabled = set(request.allowed_capabilities)
    session_clause = (
        f", the exact runtime session id {research_session_id}"
        if research_session_id is not None
        else ""
    )
    common = (
        f"For every {capability_tool_name} call: use the exact requirement_id from the "
        f"canonical request, a unique request_id{session_clause}, "
        f"round={request.current_round}, mode={request.execution_mode}, "
        "observed_at equal to pit_cutoff_at, cutoff_at equal to pit_cutoff_at, and a remaining "
        "per-call max_cost_usd that does not exceed the product budget.",
        "After each capability result, inspect the returned EvidenceCandidate records and any "
        "structured failure. Continue with another allowed capability while a hard gap has an "
        "untried ladder entry and tool/deadline budget remains. Do not stop merely because the "
        "first capability failed or returned no admissible evidence.",
    )
    hints: list[str] = list(common)
    if "official.macro" in enabled:
        hints.extend(
            (
                "official.macro discovery: for a Federal Reserve speech, first read "
                "https://www.federalreserve.gov/feeds/speeches.xml with "
                "target_url set to that URL, symbols=[], fields=[], and "
                "allowed_domains=[\"federalreserve.gov\"]. Extract the relevant official "
                "speech URL from the returned excerpt, then call official.macro again for "
                "policy.delta using that exact URL. Other official targets must remain inside "
                "the capability manifest domain allowlist.",
            )
        )
    if "market.cross_asset" in enabled:
        hints.extend(
            (
                "market.cross_asset: use target_url=null, symbols=[\"DGS2\",\"DGS10\","
                "\"DTWEXBGS\"], fields=[], allowed_domains=[\"fred.stlouisfed.org\"]. "
                "FRED is daily official data; preserve stale or independence gaps if it cannot "
                "meet the requirement freshness or source-count rule.",
            )
        )
    if "market.crypto_derivatives" in enabled:
        hints.extend(
            (
                "market.crypto_derivatives for crypto.spot: use target_url=null, "
                "symbols=[\"BTCUSDT\"], fields=[\"spot_price\",\"spot_volume\"], "
                "allowed_domains=[\"api.coinex.com\"].",
                "market.crypto_derivatives for crypto.derivatives: use target_url=null, "
                "symbols=[\"BTCUSDT\"], fields=[\"funding_rate\",\"open_interest\","
                "\"mark_price\",\"index_price\",\"basis\"], "
                "allowed_domains=[\"api.coinex.com\"].",
            )
        )
    return tuple(hints)


def build_structured_repair_prompt(
    request: ResearchSessionRequest,
    research_session_id: str,
    validation_errors: list[dict[str, object]],
    allowed_evidence_ids: Sequence[str],
) -> str:
    result_schema = json.dumps(
        ResearchSynthesisCandidate.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    errors = json.dumps(
        validation_errors,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence_whitelist = json.dumps(
        sorted(set(allowed_evidence_ids)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n".join(
        (
            "Repair only the structured synthesis from the immediately preceding turn.",
            f"Runtime session id: {research_session_id}",
            f"Canonical request id: {request.request_id}",
            "Do not call any tool, todo, or subagent. Reuse only Evidence already present "
            "in this same session. Do not invent or rewrite Evidence identifiers.",
            "Allowed Evidence ID whitelist (exact strings; use no other identifier):",
            evidence_whitelist,
            "Return exactly one JSON object. Do not add markdown fences, commentary, "
            "trailing text, or fields absent from the schema.",
            "Validation errors from the previous object (paths/types/messages only):",
            errors,
            "Canonical research-synthesis-candidate.v1 JSON Schema:",
            result_schema,
        )
    )
