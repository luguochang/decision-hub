from tools.ops.audit_pack_instructions import verify_received_prompts


def test_archive_requires_exact_prompt_and_subsequent_model_activity():
    received = {"type": "user/message", "data": {"content": [{"type": "text", "text": "prompt"}]}}
    model = {"type": "request/header", "data": {}}
    result = verify_received_prompts([(1, "prompt"), (2, "changed")], [received, model])
    assert result[0]["host_received_exact_prompt"]
    assert result[0]["model_activity_after_receipt"]
    assert not result[1]["host_received_exact_prompt"]
    assert not result[1]["model_activity_after_receipt"]
    assert not verify_received_prompts([(1, "prompt")], [model, received])[0][
        "model_activity_after_receipt"
    ]
    wrong_origin = verify_received_prompts(
        [(1, "prompt")], [{**received, "type": "tool/result"}, model]
    )
    assert not wrong_origin[0]["host_received_exact_prompt"]
