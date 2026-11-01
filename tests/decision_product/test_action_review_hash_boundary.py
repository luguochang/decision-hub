"""Real native mapping/review view hash ABI; not a full database authentication."""

import json

import pytest

from packages.kernel.decision_hub_kernel.application.action_research_reader import (
    _attach_review_effect,
    _native_action_from_originals,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review_effects import _view
from packages.kernel.decision_hub_kernel.ports.research_review_effects import (
    ResearchReviewEffectOriginal,
)
from tests.decision_product.test_action_research_native_reader import originals
from tests.decision_product.test_native_action_synthesis import sha
from tests.runtime.test_dsh_research_runtime import NOW


def _binding():
    request, raw, selected = originals()
    native = _native_action_from_originals(
        selected=selected,
        raw=raw,
        request=request,
        decision_cutoff_at=request.pit_cutoff_at,
        task_created_at=NOW,
    )
    events = json.loads(raw.reply.events_json)
    wrapper = events[3]["payload"]["event"]["data"]["result"]
    receipt = json.loads(wrapper["original_output"]["output"][0]["text"])
    mapped_ref = selected.mapping_input_ref.model_copy(
        update={
            "output_id": "mapped-native-component",
            "role": "mapped-native-component",
            "schema_version": selected.schema_version,
            "payload_hash": observation_sha(selected.model_dump(mode="json")),
        }
    )
    original = ResearchReviewEffectOriginal(
        run_id=request.run_id,
        generation=raw.context.generation,
        root_session_id=raw.context.dsh_session_id,
        mapped_ref=mapped_ref,
        transport_ref=selected.mapping_input_ref,
        selected_call_id=selected.selection.call_id,
        selected_candidate_hash=selected.selection.candidate_payload_hash,
        # This is the producer's actual complete-original hash semantics.
        source_result_hash=observation_sha(selected.result.model_dump(mode="json")),
        public_review_id=selected.result.reviews[0].review_id,
        receipt=receipt,
        receipt_hash=sha(wrapper["original_output"]["output"][0]["text"]),
        reviewer_call_id="review",
        reviewer_call_event_index=2,
        reviewer_result_event_index=3,
        reviewer_call_event_hash=observation_sha(events[2]),
        reviewer_result_event_hash=observation_sha(events[3]),
        frozen_wrapper_hash=observation_sha(wrapper),
        frozen_candidate_hash=wrapper["candidate_sha256"],
        reviewer_prompt_hash=wrapper["actual_prompt_sha256"],
        child_session_id="child",
        challenges=(),
        known_at=selected.recorded_at,
    )
    effect_ref = mapped_ref.model_copy(
        update={
            "output_id": "effect-component",
            "role": "effect-component",
            "schema_version": original.schema_version,
            "payload_hash": observation_sha(original.model_dump(mode="json")),
        }
    )
    # Exercise the real durable-original → view projection used by its reader.
    return native, selected, mapped_ref, _view(original, effect_ref)


def test_review_effect_attaches_to_complete_result_including_attached_reviews():
    native, selected, mapped_ref, effects = _binding()
    assert selected.result.reviews
    assert effects.source_result_hash != research_result_content_hash(selected.result)
    attached = _attach_review_effect(native, mapped_ref, selected, effects)
    assert attached.review_effect_ref == effects.review_effect_ref


def test_review_effect_content_only_hash_cannot_replace_complete_original_hash():
    native, selected, mapped_ref, effects = _binding()
    content_only = research_result_content_hash(selected.result)
    with pytest.raises(ValueError, match="action_native_review_original_missing_or_mismatched"):
        _attach_review_effect(
            native,
            mapped_ref,
            selected,
            effects.model_copy(update={"source_result_hash": content_only}),
        )
