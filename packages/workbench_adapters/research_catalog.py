"""Describe the executing Gateway configuration without contacting providers."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from packages.provider_adapters.routing import ProviderCapabilityRouter


def research_query_catalog(manifests, adapters, enabled):
    by_id = {item.capability_id: item for item in adapters}
    capabilities = []
    for manifest in sorted(manifests, key=lambda item: item.capability_id):
        adapter = by_id.get(manifest.capability_id)
        if (
            manifest.capability_id not in enabled
            or adapter is None
            or manifest.audit_status != "approved"
            or manifest.license_status != "approved"
        ):
            continue
        routes = []
        if isinstance(adapter, ProviderCapabilityRouter):
            for route in adapter.routes:
                child = adapter.adapters.get(route.provider_id)
                if (
                    child is None
                    or route.audit_status != "approved"
                    or route.license_status != "approved"
                ):
                    continue
                routes.append(
                    {
                        "provider_id": route.provider_id,
                        "allowed_domains": sorted(route.allowed_domains),
                        "fields": _fields(child),
                        "modes": sorted(child.supported_modes),
                        "requires_event_window": bool(route.requires_event_window),
                        **_query_inputs(child, manifest.allowed_domains, route.allowed_domains),
                    }
                )
        else:
            routes.append(
                {
                    "provider_id": None,
                    "allowed_domains": sorted(manifest.allowed_domains),
                    "fields": _fields(adapter),
                    "modes": sorted(adapter.supported_modes),
                    "requires_event_window": False,
                    **_query_inputs(adapter, manifest.allowed_domains),
                }
            )
        capabilities.append(
            {
                "capability_id": manifest.capability_id,
                "allowed_domains": sorted(manifest.allowed_domains),
                "modes": sorted(adapter.supported_modes),
                "routes": routes,
            }
        )
    payload = {"schema_version": "research-query-catalog.v1", "capabilities": capabilities}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**payload, "catalog_hash": digest}


def _fields(adapter):
    fields = getattr(adapter, "supported_fields", None)
    # Missing metadata means unknown, never unrestricted fields.
    return sorted(fields) if fields is not None else None


def _query_inputs(adapter, *domain_scopes):
    symbols = getattr(adapter, "supported_symbols", None)
    symbol_fields = getattr(adapter, "symbol_fields", None)
    sources = getattr(adapter, "source_query_catalog", None)
    for scope in domain_scopes:
        if sources is None:
            break
        narrowed = []
        for source in sources:
            domains = set()
            for declared in source["domains"]:
                for allowed in scope:
                    if declared == allowed or declared.endswith("." + allowed):
                        domains.add(declared)
                    elif allowed.endswith("." + declared):
                        domains.add(allowed)
            if domains:
                narrowed.append({**source, "domains": sorted(domains)})
        sources = narrowed
    return {
        "symbols": sorted(symbols) if symbols is not None else None,
        "symbols_required": getattr(adapter, "symbols_required", None),
        "max_symbols": getattr(adapter, "max_symbols", None),
        "symbol_fields": {k: sorted(v) for k, v in symbol_fields.items()}
        if symbol_fields is not None
        else None,
        "sources": sources,
        "target_url_required": getattr(adapter, "target_url_required", None),
        "evidence_ref_required": getattr(adapter, "evidence_ref_required", None),
        "usage_notes": getattr(adapter, "usage_notes", None),
    }


@dataclass(frozen=True)
class _CatalogScopeSnapshot:
    """Minimum immutable authority needed to narrow one catalog response.

    This projection deliberately does not authenticate the complete Task,
    Selection, Prompt or Graph chain. Those proofs remain mandatory at the
    execution and publication boundaries. A catalog GET only needs the
    already-frozen Link, Method V3 and request Pack identities; reading more
    here makes a metadata request repeat the full research preparation.
    """

    run_id: str
    dsh_session_id: str
    request_hash: str
    generation: int
    method_hash: str
    allowed_capabilities: tuple[str, ...]
    execution_mode: Literal["live", "replay"]
    deadline_at: datetime


class _CatalogScopeError(ValueError):
    """Stable fail-closed classification for an invalid scoped projection."""


def _scoped_catalog_snapshot(database, session_id: str) -> _CatalogScopeSnapshot:
    """Validate a catalog scope in one read transaction, without deep proofs.

    Link terminal state and the current Selection/Graph writer lease are not
    catalog authority.  The former deep reader did not require a current
    selection permission either.  Provider admission rechecks live Run/Link
    state in ``DurableResearchCapabilityGateway._validate_session`` and again
    under the serialized tool reservation; Graph readers separately require
    the current writer permission.  Keeping those mutable fences at execution
    avoids treating this read-only metadata response as a tool grant.
    """
    from sqlalchemy import and_, select
    from sqlalchemy.exc import MultipleResultsFound
    from sqlalchemy.orm import aliased

    from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
        ResearchRequestPackReceiptV2,
    )
    from packages.contracts_py.decision_hub_contracts.generated.research_context_execution import (
        ResearchExecutionMethodReceiptV3,
    )
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
        DshSessionLinkService,
    )
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PACK_ROLE,
        PACK_SCHEMA,
        _read_receipt,
    )
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
        _method_id,
        _read_role,
    )
    from packages.kernel.decision_hub_kernel.application.research_method import ROLE as METHOD_ROLE
    from packages.kernel.decision_hub_kernel.application.research_method import recipe_hash
    from packages.kernel.decision_hub_kernel.persistence.db import (
        DshSessionLinkRecord,
        DshSessionPromptRecord,
        RunRecord,
        RunRoleOutputRecord,
        as_utc,
    )

    method_row_type = aliased(RunRoleOutputRecord)
    pack_row_type = aliased(RunRoleOutputRecord)
    statement = (
        select(
            DshSessionLinkRecord,
            RunRecord,
            method_row_type,
            pack_row_type,
            DshSessionPromptRecord,
        )
        .outerjoin(RunRecord, RunRecord.run_id == DshSessionLinkRecord.run_id)
        .outerjoin(
            method_row_type,
            and_(
                method_row_type.run_id == DshSessionLinkRecord.run_id,
                method_row_type.role == METHOD_ROLE,
            ),
        )
        .outerjoin(
            pack_row_type,
            and_(
                pack_row_type.run_id == DshSessionLinkRecord.run_id,
                pack_row_type.role == PACK_ROLE,
            ),
        )
        .outerjoin(
            DshSessionPromptRecord,
            and_(
                DshSessionPromptRecord.run_id == DshSessionLinkRecord.run_id,
                DshSessionPromptRecord.generation == DshSessionLinkRecord.generation,
            ),
        )
        .where(DshSessionLinkRecord.dsh_session_id == session_id)
        .execution_options(populate_existing=True)
    )
    try:
        with database.session() as session:
            connection = session.connection()
            if connection.dialect.name == "sqlite":
                driver = connection.connection.driver_connection
                if not driver.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            found = session.execute(statement).one_or_none()
            if found is None:
                raise _CatalogScopeError("research_catalog_session_unknown")
            link_row, run, method_row, pack_row, prompt_row = found
            if run is None or run.strategy_version != "research.v1":
                raise _CatalogScopeError("research_catalog_run_invalid")
            if method_row is None or method_row.schema_version != "research-execution-method.v3":
                raise _CatalogScopeError("research_catalog_method_v3_required")
            if pack_row is None or pack_row.schema_version != PACK_SCHEMA:
                raise _CatalogScopeError("research_catalog_pack_v2_required")

            # These parsers authenticate only the selected immutable rows. They
            # intentionally do not call read_prepared_execution_in_session or
            # read_research_bundle_in_session/_validate_current_source.
            method = _read_role(
                method_row,
                ResearchExecutionMethodReceiptV3,
                role=METHOD_ROLE,
                output_id=_method_id(run.run_id),
            )
            pack = _read_receipt(pack_row, PACK_ROLE, ResearchRequestPackReceiptV2)
            link = DshSessionLinkService._view(link_row)  # pyright: ignore[reportPrivateUsage]

            expected_session, expected_request = DshSessionLinkService.deterministic_ids(
                run.run_id, link.request_hash, link.generation
            )
            upstream = method.definition.upstream_identity.model_dump(mode="json")
            canonical_upstream = json.dumps(
                upstream, sort_keys=True, separators=(",", ":")
            )
            allowed = tuple(method.definition.allowed_capabilities)
            pack_identity = method.definition.bundle.pack.model_dump(mode="python")
            actual_pack_identity = {
                "output_id": pack_row.output_id,
                "run_id": pack_row.run_id,
                "role": pack_row.role,
                "row_schema_version": pack_row.schema_version,
                "payload_hash": pack_row.payload_hash,
                "recorded_at": as_utc(pack_row.created_at),
            }
            definition = method.definition
            if (
                link.run_id != run.run_id
                or link.dsh_session_id != session_id
                or link.dsh_session_id != expected_session
                or type(link.generation) is not int
                or link.generation < 1
                or prompt_row is None
                or prompt_row.run_id != run.run_id
                or prompt_row.generation != link.generation
                or prompt_row.dsh_session_id != link.dsh_session_id
                or prompt_row.dsh_session_id != expected_session
                or prompt_row.request_id != expected_request
                or prompt_row.request_hash != link.request_hash
                or method.run_id != run.run_id
                or method.request_hash != link.request_hash
                or method.method_hash != recipe_hash(definition.model_dump(mode="json"))
                or method.recorded_at != as_utc(method_row.created_at)
                or method.recorded_at != link.created_at
                or run.runtime_version != definition.runtime_version
                or canonical_upstream != link_row.upstream_identity_json
                or upstream != link.upstream_identity.model_dump(mode="json")
                or upstream["plugin_build_hash"] != link_row.plugin_build_hash
                or definition.deadline_at != link.deadline_at
                or definition.execution_budget.max_tool_calls != link.max_tool_calls
                or definition.execution_budget.per_model_step_timeout_seconds * 1000
                != link.model_step_timeout_ms
            ):
                raise _CatalogScopeError("research_catalog_link_method_mismatch")
            if (
                not allowed
                or allowed != tuple(sorted(set(allowed)))
                or definition.execution_mode not in {"live", "replay"}
                or definition.bundle.run_id != run.run_id
                or definition.policy_binding.run_id != run.run_id
                or pack.run_id != run.run_id
                or pack_identity != actual_pack_identity
                or pack.definition_hash
                != recipe_hash(pack.definition.model_dump(mode="json"))
                or definition.request_pack_hash != pack.definition_hash
                or definition.policy_binding.request_pack_hash != pack.definition_hash
                or definition.execution_mode != pack.definition.execution_mode
                or not set(allowed).issubset(pack.definition.allowed_capabilities)
            ):
                raise _CatalogScopeError("research_catalog_method_pack_mismatch")
            return _CatalogScopeSnapshot(
                run_id=run.run_id,
                dsh_session_id=link.dsh_session_id,
                request_hash=link.request_hash,
                generation=link.generation,
                method_hash=method.method_hash,
                allowed_capabilities=allowed,
                execution_mode=definition.execution_mode,
                deadline_at=definition.deadline_at,
            )
    except _CatalogScopeError:
        raise
    except (ValueError, KeyError, TypeError, MultipleResultsFound) as exc:
        raise _CatalogScopeError("research_catalog_scope_receipt_invalid") from exc


def scoped_research_query_catalog(catalog, database, session_id):
    """Narrow by immutable V3 authority; missing or legacy inputs fail closed."""
    scope = _scoped_catalog_snapshot(database, session_id)
    allowed = scope.allowed_capabilities
    mode = scope.execution_mode
    capabilities = []
    for item in catalog["capabilities"]:
        if item["capability_id"] not in allowed or mode not in item["modes"]:
            continue
        capabilities.append(
            {
                **item,
                "modes": [mode],
                "routes": [
                    {**route, "modes": [mode]} for route in item["routes"] if mode in route["modes"]
                ],
            }
        )
    payload = {
        "schema_version": "research-query-catalog.v1",
        "capabilities": capabilities,
        "scope": {
            "run_id": scope.run_id,
            "dsh_session_id": scope.dsh_session_id,
            "request_hash": scope.request_hash,
            "generation": scope.generation,
            "status": "bound",
            "method_hash": scope.method_hash,
            "execution_mode": mode,
            "deadline_at": scope.deadline_at.isoformat(),
        },
    }
    return {
        **payload,
        "catalog_hash": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
