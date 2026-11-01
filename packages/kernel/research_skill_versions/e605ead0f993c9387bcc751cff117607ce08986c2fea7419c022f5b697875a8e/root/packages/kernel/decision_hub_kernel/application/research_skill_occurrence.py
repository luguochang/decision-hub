"""Shared original-backed DSH tool occurrence; pointers are never native sequences."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputRef,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    read_provider_emission_in_session,
    read_provider_input_in_session,
    strict_json,
    tool_arguments_json,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc

from .research_skill_package import encoded, sha


def block_hash(value):
    return sha(tool_arguments_json(value).encode())


@dataclass(frozen=True)
class ToolOccurrence:
    arguments: dict[str, Any]
    result: Any
    provider_input: Any
    input_ref: ObservedExecutionRef
    emission: Any
    emission_ref: ObservedExecutionRef
    transcript: dict[str, Any]
    call_message_index: int
    result_message_index: int


def read_occurrence(
    session,
    proof,
    *,
    run_id,
    generation,
    root_session_id,
    execution_session_id,
    request_hash,
    tool_name,
    provider_reader=read_provider_input_in_session,
    emission_reader=read_provider_emission_in_session,
):
    if proof["kind"] != "provider_input_transcript.v1":
        raise ValueError("skill_native_transport_reader_not_installed")
    original_ref = ObservedExecutionRef.model_validate_json(encoded(proof["input_ref"]))
    original, read_ref = provider_reader(
        session,
        ResearchProviderInputRef(
            output_id=original_ref.output_id, payload_hash=original_ref.payload_hash
        ),
    )
    submit = original.submit
    if (
        read_ref != original_ref
        or submit.run_id != run_id
        or submit.generation != generation
        or submit.root_session_id != root_session_id
        or submit.execution_session_id != execution_session_id
        or submit.parent_session_id is not None
        or root_session_id != execution_session_id
        or submit.request_hash != request_hash
        or submit.dispatch_id != proof["dispatch_id"]
        or sha(submit.input_json.encode()) != submit.input_hash
    ):
        raise ValueError("skill_loaded_provider_identity_mismatch")
    body = strict_json(submit.input_json)
    call_locus = re.fullmatch(
        r"/messages/(0|[1-9][0-9]*)/content/(0|[1-9][0-9]*)", proof["call_json_pointer"]
    )
    result_locus = re.fullmatch(r"/messages/(0|[1-9][0-9]*)", proof["result_json_pointer"])
    if call_locus is None or result_locus is None:
        raise ValueError("skill_loaded_transcript_locus_invalid")
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        raise ValueError("skill_loaded_transcript_invalid")
    calls, results = [], []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or not isinstance(message.get("source"), dict):
            continue
        if (
            message["source"].get("kind") == "tool"
            and message["source"].get("callId") == proof["call_id"]
        ):
            results.append((index, message))
        if not isinstance(message.get("content"), list):
            continue
        for bi, block in enumerate(message["content"]):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool-call"
                and block.get("id") == proof["call_id"]
            ):
                calls.append((index, bi, block, message))
    if len(calls) != 1 or len(results) != 1:
        raise ValueError("skill_loaded_transcript_occurrence_ambiguous")
    ci, bi, call, call_message = calls[0]
    ri, result_message = results[0]
    if (
        ci != int(call_locus[1])
        or bi != int(call_locus[2])
        or ri != int(result_locus[1])
        or ci >= ri
        or call_message["source"].get("kind") != "model"
        or call_message.get("role") != "assistant"
        or result_message.get("role") != "user"
        or proof["tool_name"] != tool_name
        or call.get("name") != tool_name
        or block_hash(call) != proof["call_hash"]
        or block_hash(result_message) != proof["result_hash"]
    ):
        raise ValueError("skill_loaded_transcript_mismatch")
    arguments = strict_json(call.get("arguments"))
    if not isinstance(arguments, dict):
        raise ValueError("skill_loaded_call_arguments_invalid")
    content = result_message.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "tool-result"
        or content[0].get("toolCallId") != proof["call_id"]
        or content[0].get("isError") is not False
        or result_message.get("isError") is True
    ):
        raise ValueError("skill_loaded_result_not_exact_package")
    inner = content[0].get("content")
    if (
        not isinstance(inner, list)
        or len(inner) != 1
        or not isinstance(inner[0], dict)
        or inner[0].get("type") != "text"
        or inner[0].get("isError") is True
    ):
        raise ValueError("skill_loaded_result_not_exact_package")
    result = strict_json(inner[0].get("text"))
    pair = emission_reader(
        session,
        run_id=run_id,
        generation=generation,
        root_session_id=root_session_id,
        call_id=proof["call_id"],
        execution_session_id=execution_session_id,
    )
    if pair is None:
        raise ValueError("skill_loaded_provider_emission_missing")
    emission, emission_ref = pair
    emitted = emission.submit
    if (
        emitted.call_id != proof["call_id"]
        or emitted.tool_name != proof["tool_name"]
        or emitted.arguments_hash != block_hash(arguments)
        or strict_json(emitted.raw_arguments_json) != arguments
        or emitted.run_id != submit.run_id
        or emitted.generation != submit.generation
        or emitted.root_session_id != submit.root_session_id
        or emitted.execution_session_id != submit.execution_session_id
        or emitted.request_hash != submit.request_hash
        or emitted.dispatch_id == submit.dispatch_id
        or aware_utc(emission.known_at) > aware_utc(original.known_at)
    ):
        raise ValueError("skill_loaded_provider_emission_mismatch")
    return ToolOccurrence(
        arguments, result, original, read_ref, emission, emission_ref, body, ci, ri
    )
