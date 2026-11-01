"""Project historical configuration from Run receipts, never today's Pack."""

from typing import Literal, TypedDict

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DomainPackManifest
from packages.kernel.decision_hub_kernel.application.research_pack import (
    ROLE,
    ResearchPackService,
)
from packages.kernel.decision_hub_kernel.application.research_value_basis import SavedCoverageBasis
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)


class SavedConfiguration(TypedDict):
    domain_pack_ref: str | None
    domain_pack_version: str | None
    role_profile_ref: str | None
    capability_versions: list[str]
    gate_policy_ref: str | None
    source_registry_ref: str | None
    pack_basis: Literal["saved_pack", "unavailable", "invalid"]
    request_basis: Literal["saved_request", "unavailable", "invalid"]
    unverified_fields: list[str]
    pack_receipt_ref: str | None


def saved_configuration(
    session: Session,
    run: RunRecord,
    basis: SavedCoverageBasis,
    *,
    pack_service: ResearchPackService,
) -> SavedConfiguration:
    request = basis.request
    pack = None
    reference = None
    pack_basis: Literal["saved_pack", "unavailable", "invalid"] = "unavailable"
    row = (
        session.query(RunRoleOutputRecord)
        .filter_by(run_id=run.run_id, role=ROLE)
        .populate_existing()
        .one_or_none()
    )
    try:
        # The existing borrowed reader validates complete V2 bundles and keeps
        # the V1 receipt path. A partial V2 bundle must not become unavailable.
        body = pack_service.get_in_session(session, run.run_id)
        if body is not None:
            if row is None:
                raise ValueError("saved_pack_identity_missing")
            candidate = DomainPackManifest.model_validate(body["definition"]["manifest"])
            prompt = session.get(DshSessionPromptRecord, (run.run_id, 1), populate_existing=True)
            pack_ref = f"{candidate.pack_id}.v{candidate.version.split('.')[0]}"
            if (
                (prompt and as_utc(row.created_at) > as_utc(prompt.created_at))
                or (request and request.domain_pack_ref != pack_ref)
            ):
                raise ValueError("saved_pack_identity_mismatch")
            pack = candidate
            reference = row.output_id
            pack_basis = "saved_pack"
    except (ValueError, KeyError, TypeError):
        pack_basis = "invalid"
    # A Pack is the declared policy, not proof of the final execution budget.
    # Historical-review overrides are present only in the submitted request.
    domain_ref = request.domain_pack_ref if request else (
        f"{pack.pack_id}.v{pack.version.split('.')[0]}" if pack else None
    )
    if basis.status == "saved_request":
        request_basis: Literal["saved_request", "unavailable", "invalid"] = "saved_request"
    elif basis.status == "unavailable":
        request_basis = "unavailable"
    elif basis.status == "invalid":
        request_basis = "invalid"
    else:
        raise ValueError("saved_configuration_request_basis_unknown")
    fields: SavedConfiguration = {
        "domain_pack_ref": domain_ref,
        "domain_pack_version": pack.version if pack else None,
        "role_profile_ref": request.role_profile_ref if request else None,
        "gate_policy_ref": pack.gate_policy_ref if pack else None,
        # These versions were not frozen in the request/Pack receipt. An allowed
        # capability ID is not a version, and today's registry proves nothing.
        "capability_versions": [],
        "source_registry_ref": None,
        "pack_basis": pack_basis,
        "request_basis": request_basis,
        "pack_receipt_ref": reference,
        # A saved manifest reference proves which policy was declared, not that
        # its content/checks were consumed. Keep that execution claim unknown.
        "unverified_fields": [],
    }
    fields["unverified_fields"] = [
        name for name, value in (
            ("domain_pack_ref", fields["domain_pack_ref"]),
            ("domain_pack_version", fields["domain_pack_version"]),
            ("role_profile_ref", fields["role_profile_ref"]),
            ("gate_policy_ref", fields["gate_policy_ref"]),
            ("capability_versions", fields["capability_versions"]),
            ("source_registry_ref", fields["source_registry_ref"]),
        )
        if not value or name == "gate_policy_ref"
    ]
    return fields
