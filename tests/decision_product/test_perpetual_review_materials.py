"""Real frozen Task/server originals and actual ResearchEvidenceService admission."""

from copy import deepcopy
from datetime import timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import EvidenceCandidate
from packages.kernel.decision_hub_kernel.application import research_evidence
from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    read_perpetual_research_materials_in_session,
)
from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
    read_review_originals_in_session,
    select_review_originals,
    validate_analysis_originals,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_native_graph import packet_for
from tests.decision_product.test_perpetual_research_binding import AT, factory_for
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_real_selected_originals_aliases_cutoff_and_later_evidence(
    binding_case, tmp_path, monkeypatch
):
    case = binding_case
    factory, _ = factory_for(case, tmp_path)
    request = factory.build(case["event_id"], case["run_id"])
    with case["db"].session() as session:
        materials = read_perpetual_research_materials_in_session(session, request)
    requirement = request.evidence_requirements[0]

    def admit(name, seconds):
        at = AT + timedelta(seconds=seconds)
        monkeypatch.setattr(research_evidence, "utcnow", lambda: at)
        item = EvidenceCandidate(
            evidence_id=name,
            requirement_id=requirement.requirement_id,
            kind="official",
            authority="official",
            source_id="synthetic-accepted-original",
            source_url="https://example.com/original",
            published_at=at,
            observed_at=at,
            received_at=at,
            content_hash=observation_sha(dict(name=name)),
            excerpt="测试已接收原件：不得将其视为真实市场因果结论。",
            structured_payload_ref=None,
            tool_call_id=None,
            research_session_id="actual-original-source-fixture",
            round=1,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        result = ResearchEvidenceService(case["db"]).accept_candidates(
            run_id=request.run_id,
            capability_id=request.allowed_capabilities[0],
            candidates=[item],
            requirements={requirement.requirement_id: requirement},
            cutoff_at=at,
        )
        assert result[0].quality == "accepted"

    admit("first-source", 1)
    with case["db"].session() as session:
        catalog = read_review_originals_in_session(
            session, request, materials, as_of=AT + timedelta(seconds=2)
        )
    first = next(row for row in catalog["originals"] if row["role"] == "first-source")
    frozen = next(row for row in catalog["originals"] if row["source_kind"] == "frozen_input")
    synthesis = packet_for(request, materials)["synthesis"]
    synthesis["reasoning_refs"] = [first["original_ref"]]
    synthesis["analysis"]["main_chain"][0]["evidence_refs"] = [
        first["original_ref"],
        frozen["source_ref"],
    ]
    selected = select_review_originals(synthesis, catalog)
    validate_analysis_originals(synthesis, selected)
    admit("unrelated-later-source", 4)
    with case["db"].session() as session:
        assert (
            read_review_originals_in_session(
                session, request, materials, as_of=AT + timedelta(seconds=2), selected=selected
            )
            == selected
        )
        changed = deepcopy(selected)
        item = next(row for row in changed["originals"] if row["role"] == "first-source")
        body = __import__("json").loads(item["body_json"])
        body["excerpt"] = "被替换的来源文本"
        item.update(
            body_json=observation_json(body),
            source_ref=dict(ref=item["source_ref"]["ref"], hash=observation_sha(body)),
        )
        with pytest.raises(ValueError, match="changed_or_unavailable"):
            read_review_originals_in_session(
                session, request, materials, as_of=AT + timedelta(seconds=2), selected=changed
            )
        foreign = dict(selected, run_id="another-run")
        with pytest.raises(ValueError, match="scope_mismatch"):
            read_review_originals_in_session(
                session, request, materials, as_of=AT + timedelta(seconds=2), selected=foreign
            )
        later = read_review_originals_in_session(
            session, request, materials, as_of=AT + timedelta(seconds=5)
        )
        future = deepcopy(selected)
        future["originals"].append(
            next(row for row in later["originals"] if row["role"] == "unrelated-later-source")
        )
        with pytest.raises(ValueError, match="changed_or_unavailable"):
            read_review_originals_in_session(
                session, request, materials, as_of=AT + timedelta(seconds=2), selected=future
            )
