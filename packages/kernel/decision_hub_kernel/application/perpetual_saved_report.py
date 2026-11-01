"""Signed full-original read of the existing V5 Artifact, without V1 quality grants."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult

from .development_history_reader import DevelopmentHistoryReader

from ..persistence.db import (
    ArtifactRecord,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from ..ports.event_scenario_manifest import event_scenario_validation_scope
from ..ports.market_path import aware_utc
from ..ports.research_action_synthesis import primary_perpetual_synthesis
from .perpetual_research_binding import ADMISSION_ROLE, read_perpetual_admission_identity_in_session
from .provider_input_receipts import strict_json
from .research_graph_binding import parse_graph_final_ref
from .research_publication_binding import observation_json

SOURCE_SCHEMA = "research-publication-source.v5"
SOURCE_SCHEMA_V6 = "research-publication-source.v6"
SOURCE_ROLE = "research_publication_source"


def _probability_definitions_view(*, owner_id: str, run_id: str, source: dict, cached: dict):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionModelAssumptionV1,
    )

    from ..ports.perpetual_preparation import derived_ref

    packet = source["candidate"]["perpetual_synthesis"]
    binding, synthesis = packet["binding"], packet["synthesis"]
    if source["perpetual_binding"] != binding or cached.get("synthesis_ref") != binding[
        "synthesis_ref"
    ]:
        raise ValueError("perpetual_probability_definitions_binding_mismatch")
    common = dict(
        owner_id=owner_id,
        run_id=run_id,
        mapped_ref=binding["mapped_ref"],
        provider_emission_ref=binding["provider_emission_ref"],
        synthesis_ref=binding["synthesis_ref"],
    )
    status = cached.get("status")
    if status == "unavailable":
        if set(cached) != {"status", "synthesis_ref"}:
            raise ValueError("perpetual_probability_definitions_cache_invalid")
        return dict(
            schema_version="perpetual-probability-definitions.v1",
            status="unavailable",
            binding=common,
            reason_code="prepared_probability_definitions_unavailable",
        )
    if status != "available" or set(cached) != {
        "status",
        "synthesis_ref",
        "prepare_emission_ref",
        "definitions",
    }:
        raise ValueError("perpetual_probability_definitions_cache_invalid")
    definitions = cached["definitions"]
    roles = ("event_definition", "occurrence_target", "outcome_target")
    event_probability = primary_perpetual_synthesis(synthesis)["event_probability"]
    expected_refs = (
        event_probability["event_definition_ref"],
        event_probability["occurrence_target_ref"],
        event_probability["outcome_target_ref"],
    )
    if (
        not isinstance(definitions, list)
        or len(definitions) != len(roles)
        or event_probability["occurrence_distribution"]["target_ref"]
        != expected_refs[1]
        or event_probability["outcome_given_occurrence"]["target_ref"]
        != expected_refs[2]
    ):
        raise ValueError("perpetual_probability_definitions_original_mismatch")
    verified = []
    for row, role, expected_ref in zip(definitions, roles, expected_refs, strict=True):
        if not isinstance(row, dict) or set(row) != {
            "role",
            "definition_ref",
            "definition",
        }:
            raise ValueError("perpetual_probability_definitions_original_mismatch")
        definition = DecisionModelAssumptionV1.model_validate(row["definition"]).model_dump(
            mode="json"
        )
        reference = derived_ref("decision_model_assumption", definition)
        if (
            row["role"] != role
            or definition["role"] != role
            or row["definition_ref"] != reference
            or reference != expected_ref
        ):
            raise ValueError("perpetual_probability_definitions_original_mismatch")
        verified.append(
            dict(role=role, definition_ref=reference, definition=definition)
        )
    from ..ports.research_observation import ObservedExecutionRef

    prepare_original = ObservedExecutionRef.model_validate_json(
        observation_json(cached["prepare_emission_ref"])
    )
    provider_original = ObservedExecutionRef.model_validate_json(
        observation_json(binding["provider_emission_ref"])
    )
    prepare_ref = prepare_original.model_dump(mode="json")
    if (
        prepare_ref["schema_version"] != "research-provider-tool-emission.v1"
        or prepare_original.recorded_at > provider_original.recorded_at
    ):
        raise ValueError("perpetual_probability_definitions_prepare_original_mismatch")
    return dict(
        schema_version="perpetual-probability-definitions.v1",
        status="available",
        binding=dict(common, prepare_emission_ref=prepare_ref),
        definitions=verified,
    )


def _report_definition_views(
    reader: Any, session: Any, *, owner_id: str, run_id: str, source: dict[str, Any],
    known_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Keep a registered historical reader on its original API and source closure."""
    binding = source["perpetual_binding"]
    read_combined = getattr(reader, "read_perpetual_report_definitions_in_session", None)
    if read_combined is None:
        cached = reader.read_perpetual_probability_definitions_in_session(
            session, mapped_ref=binding["mapped_ref"],
            provider_emission_ref=binding["provider_emission_ref"], known_at=known_at,
        )
        candidate_cache = None
    else:
        caches = read_combined(
            session, mapped_ref=binding["mapped_ref"],
            provider_emission_ref=binding["provider_emission_ref"], known_at=known_at,
        )
        cached, candidate_cache = caches["probability_definitions"], caches["candidate_definitions"]
    probability = _probability_definitions_view(
        owner_id=owner_id, run_id=run_id, source=source, cached=cached,
    )
    if candidate_cache is None:
        try:
            from .perpetual_candidate_definitions import legacy_candidate_definitions
        except ModuleNotFoundError as error:
            if error.name != f"{__package__}.perpetual_candidate_definitions":
                raise
        else:
            candidate_cache = legacy_candidate_definitions(
                reader, session, source=source, cached=cached,
            )
    candidates = None
    if candidate_cache is not None:
        from .perpetual_candidate_definitions import candidate_definitions_view

        candidates = candidate_definitions_view(
            owner_id=owner_id, run_id=run_id, source=source, cached=candidate_cache,
        )
    return probability, candidates


class PerpetualSavedReportQuery:
    def __init__(self, database, *, now=utcnow, historical_reader: DevelopmentHistoryReader | None = None):
        self.database, self.now = database, now
        self.historical_reader = historical_reader

    def applicable(self, run_id):
        """Content-free version classification; existing public Run metadata only."""
        with self.database.session() as session:
            return (
                session.scalar(
                    select(RunRoleOutputRecord.output_id)
                    .where(
                        RunRoleOutputRecord.run_id == run_id,
                        RunRoleOutputRecord.role == ADMISSION_ROLE,
                    )
                    .limit(1)
                )
                is not None
            )

    @event_scenario_validation_scope()
    def read(self, owner_id, run_id):
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            at = aware_utc(self.now())
            identity = read_perpetual_admission_identity_in_session(session, run_id)
            if identity is None:
                raise ValueError("perpetual_report_not_applicable")
            admission, admission_ref = identity
            if admission["owner_id"] != owner_id:
                raise PermissionError("perpetual_report_owner_mismatch")
            run = session.get(RunRecord, run_id, populate_existing=True)
            source = session.scalar(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == SOURCE_ROLE,
                )
                .execution_options(populate_existing=True)
            )
            if (
                source is None
                or run.artifact_id is None
                or run.finished_at is None
                or source.schema_version not in {SOURCE_SCHEMA, SOURCE_SCHEMA_V6}
            ):
                raise ValueError("perpetual_report_not_saved")
            if as_utc(source.created_at) > at or as_utc(run.finished_at) > at:
                raise ValueError("perpetual_report_not_saved")
            body = strict_json(source.payload_json)
            result = session.get(ResearchResultRecord, run_id, populate_existing=True)
            if result is None:
                raise ValueError("perpetual_report_result_missing")
            declared = ResearchSessionResult.model_validate_json(result.payload_json)
            from .commit import _artifact_body, _read_publication_source_in_session

            graph_final_ref = parse_graph_final_ref(body["graph_final_ref"])
            scope = (
                self.historical_reader.publication_scope(session, run_id, graph_final_ref.model_dump_json())
                if self.historical_reader is not None else nullcontext()
            )
            with scope:
                artifact_id, _gate = _read_publication_source_in_session(
                    session,
                    run=run,
                    reference=graph_final_ref,
                    declared=declared,
                    expected_lease_owner=None,
                    candidate=None,
                    gate=None,
                )
            artifact = session.get(ArtifactRecord, artifact_id, populate_existing=True)
            raw_artifact = observation_json(_artifact_body(artifact))
            binding = body["perpetual_binding"]
            from . import perpetual_research_reader

            probability_definitions, candidate_definitions = _report_definition_views(
                perpetual_research_reader, session, owner_id=owner_id, run_id=run_id,
                source=body, known_at=graph_final_ref.row.recorded_at,
            )
            from .perpetual_saved_action import read_saved_perpetual_action_in_session

            action_publication = read_saved_perpetual_action_in_session(
                session, run=run, admission=admission, admission_ref=admission_ref,
                report_binding=binding,
                report_synthesis=body["candidate"]["perpetual_synthesis"]["synthesis"],
                report_review_request=body["candidate"]["perpetual_synthesis"]["review_request"],
                report_published_at=as_utc(source.created_at), read_at=at,
            )
            return dict(
                schema_version="perpetual-saved-report.v1",
                read_scope="full_saved_perpetual_original",
                owner_id=owner_id,
                run_id=run_id,
                event_id=run.event_id,
                artifact_id=artifact_id,
                source_json=source.payload_json,
                source_sha256=source.payload_hash,
                publication_ref=dict(
                    output_id=source.output_id,
                    role=source.role,
                    schema_version=source.schema_version,
                    payload_hash=source.payload_hash,
                    recorded_at=as_utc(source.created_at).isoformat(),
                ),
                artifact_json=raw_artifact,
                artifact_sha256=hashlib.sha256(raw_artifact.encode()).hexdigest(),
                published_at=as_utc(source.created_at).isoformat(),
                read_at=at.isoformat(),
                financial_qualified=False,
                execution_authorized=False,
                bundle_export_status="unsupported_perpetual_version",
                probability_definitions=probability_definitions,
                **({"candidate_definitions": candidate_definitions}
                   if candidate_definitions is not None else {}),
                action_publication=action_publication.model_dump(mode="json"),
            )
