"""Read existing V5 report originals, never grant V1 quality or export authority."""

import importlib
import importlib.util

import pytest

from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleJobRecord,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import OWNER, freeze
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_saved_report"


def test_saved_perpetual_reader_requires_actual_owner_before_report_body(binding_case):
    assert importlib.util.find_spec(MODULE), "actual signed V5 report reader is missing"
    case = binding_case
    freeze(case)
    query = importlib.import_module(MODULE).PerpetualSavedReportQuery(case["db"])
    assert query.applicable(case["run_id"])
    with pytest.raises(PermissionError, match="perpetual_report_owner_mismatch"):
        query.read("foreign-owner", case["run_id"])
    with pytest.raises(ValueError, match="perpetual_report_not_saved"):
        query.read(OWNER, case["run_id"])
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for

    paired, _, collector = client_for(case)
    anonymous, _, _ = client_for(case, pair=False)
    path = BASE + "/research/runs/" + case["run_id"] + "/report"
    assert anonymous.get(path).status_code == 403
    assert paired.get(path).status_code == 409
    assert paired.get(BASE + "/research/runs/not-a-perpetual-run/report").status_code == 404
    assert collector.calls == 0


def test_saved_report_returns_exact_prepared_probability_definitions(
    binding_case, tmp_path, monkeypatch
):
    assert PublicationBundleJobRecord.__tablename__ == "research_publication_bundle_jobs"
    from tests.decision_product.test_perpetual_action_publication import (
        assert_actual_report_then_unique_perpetual_action,
        authenticate_provider,
        lawful_packet_for,
    )
    from tests.decision_product.test_perpetual_native_graph import (
        build_perpetual_native_graph_case,
    )

    value = build_perpetual_native_graph_case(
        binding_case,
        tmp_path,
        monkeypatch,
        packet_builder=lawful_packet_for,
        before_graph=lambda prepared: authenticate_provider(prepared, monkeypatch),
        phased=True,
    )
    assert_actual_report_then_unique_perpetual_action(value, monkeypatch)


def assert_saved_report_from_actual_case(value, artifact_id, monkeypatch, *, example_path=None):
    """Called once by the existing genuine pipeline gate; no second native fixture."""
    import hashlib
    import json
    from datetime import timedelta
    from pathlib import Path

    from sqlalchemy import event

    from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import (
        PerpetualSavedReportQuery,
    )
    from packages.kernel.decision_hub_kernel.application.research_publication_frozen_reader import (
        read_registered_original,
    )
    from tests.decision_product.test_perpetual_research_binding import AT
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for

    case = value["case"]
    client, _, collector = client_for(case, clock=lambda: AT + timedelta(seconds=50))
    anonymous, _, _ = client_for(case, pair=False, clock=lambda: AT + timedelta(seconds=50))
    path = BASE + "/research/runs/" + value["request"].run_id + "/report"
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(case["db"].engine, "before_cursor_execute", record)
    try:
        assert anonymous.get(path).status_code == 403
        response = client.get(path)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"] == value["request"].run_id and body["artifact_id"] == artifact_id
        assert body["read_scope"] == "full_saved_perpetual_original"
        assert body["financial_qualified"] is body["execution_authorized"] is False
        artifact = json.loads(body["artifact_json"])
        payload = json.loads(artifact["payload_json"])
        assert payload["perpetual_synthesis"]["synthesis"]["analysis"]["thesis"]
        assert payload["facts"] or payload["inferences"]
        definitions = body["probability_definitions"]
        assert definitions["schema_version"] == "perpetual-probability-definitions.v1"
        packet = payload["perpetual_synthesis"]
        if "provider_prepare_emission" not in value:
            assert definitions == {
                "schema_version": "perpetual-probability-definitions.v1",
                "status": "unavailable",
                "binding": {
                    "owner_id": body["owner_id"],
                    "run_id": body["run_id"],
                    "mapped_ref": packet["binding"]["mapped_ref"],
                    "provider_emission_ref": packet["binding"]["provider_emission_ref"],
                    "synthesis_ref": packet["binding"]["synthesis_ref"],
                },
                "reason_code": "prepared_probability_definitions_unavailable",
            }
        else:
            assert definitions["status"] == "available"
            _assert_available_probability_definitions(
                body=body,
                packet=packet,
                definitions=definitions,
                value=value,
            )
        assert body["bundle_export_status"] == "unsupported_perpetual_version"
        download = client.get(path + "/source")
        assert download.status_code == 200
        assert download.content == body["source_json"].encode()
        assert download.headers["etag"] == '"' + hashlib.sha256(download.content).hexdigest() + '"'
        with pytest.raises(PermissionError, match="perpetual_report_owner_mismatch"):
            PerpetualSavedReportQuery(case["db"]).read("foreign-owner", value["request"].run_id)
        with case["db"].session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with pytest.raises(
                ValueError,
                match="publication_bundle_original_not_available|research_quality_publication_original_missing",
            ):
                read_registered_original(
                    session, value["request"].run_id, as_of=AT + timedelta(seconds=50)
                )
    finally:
        event.remove(case["db"].engine, "before_cursor_execute", record)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE"}
    assert collector.calls == 0
    if "provider_prepare_emission" not in value and example_path is None:
        return
    output_path = Path(
        example_path
        or "artifacts/d2-probability-definitions/available-saved-report.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2), "utf-8"
    )


def _assert_available_probability_definitions(*, body, packet, definitions, value):
    import hashlib
    import json

    assert definitions["schema_version"] == "perpetual-probability-definitions.v1"
    assert definitions["status"] == "available"
    assert definitions["binding"] == {
        "owner_id": body["owner_id"],
        "run_id": body["run_id"],
        "mapped_ref": packet["binding"]["mapped_ref"],
        "provider_emission_ref": packet["binding"]["provider_emission_ref"],
        "prepare_emission_ref": value["provider_prepare_emission"].reference.model_dump(
            mode="json"
        ),
        "synthesis_ref": packet["binding"]["synthesis_ref"],
    }
    event_probability = packet["synthesis"]["event_probability"]
    expected = [
        ("event_definition", event_probability["event_definition_ref"]),
        ("occurrence_target", event_probability["occurrence_target_ref"]),
        ("outcome_target", event_probability["outcome_target_ref"]),
    ]
    assert [row["role"] for row in definitions["definitions"]] == [
        role for role, _ in expected
    ]
    for row, (role, reference) in zip(
        definitions["definitions"], expected, strict=True
    ):
        assert row["definition"]["role"] == role
        assert row["definition_ref"] == reference
        digest = hashlib.sha256(
            json.dumps(
                row["definition"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        assert row["definition_ref"] == {
            "ref": "decision_model_assumption_" + digest,
            "hash": digest,
        }
    assert event_probability["occurrence_distribution"]["target_ref"] == expected[1][1]
    assert event_probability["outcome_given_occurrence"]["target_ref"] == expected[2][1]
