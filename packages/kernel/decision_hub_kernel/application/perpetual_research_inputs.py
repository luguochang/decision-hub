"""One explicit manual authority and immutable current-paper input binding per Run."""

from __future__ import annotations

import hashlib
import json
import weakref
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
    DecisionSettingsValuesV1,
    DecisionSourceRefV1,
    PerpetualResearchCommandV3,
    PerpetualResearchReceiptV3,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.db import ProductCommandRecord, RunRecord, RunRoleOutputRecord, as_utc
from ..persistence.perpetual_paper_repository import PerpetualPaperRepository
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_research_inputs import (
    INPUT_POLICY,
    AuthenticatedResearchInputs,
    PreparedPerpetualAdmissionV2,
    PreparedResearchAccountInputsV1,
    ResearchAccountBindingV1,
    ResearchAutomaticInputAuthorityWitnessV1,
    ResearchEffectiveRefsV1,
    ResearchInputAuthorityWitness,
    ResearchInputAuthorityWitnessV1,
    ResearchInputDerivationV1,
)
from .decision_settings import DecisionSettingsService
from .okx_perpetual_source import OKXPerpetualOriginalReader

ACCOUNT_BINDING_ROLE = "perpetual_research_account_binding_v1"
_DYNAMIC = ("account_ref", "position_scenario", "hash")
_METHOD = dict(
    schema_version="perpetual-current-paper-input-derivation.v1",
    account="authenticated_specified_paper_ledger",
    preferences="exact_saved_revision",
    utility_changed_fields=list(_DYNAMIC),
    unknowns="preserved",
)
_AUTHORITIES = {}


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _binding_id(run_id):
    return "perp_account_input_" + canonical_hash(run_id)


def _reference(binding):
    return DecisionSourceRefV1(
        ref=_binding_id(binding.run_id), hash=canonical_hash(binding.model_dump(mode="json"))
    )


def _implementation_hash():
    root = Path(__file__).resolve().parents[1]
    return canonical_hash(
        {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "application/perpetual_research_inputs.py",
                "application/perpetual_paper.py",
                "ports/perpetual_research_inputs.py",
            )
        }
    )


def _derivation():
    return ResearchInputDerivationV1(
        method_ref=DecisionSourceRefV1(
            ref="perpetual-current-paper-input-derivation.v1", hash=canonical_hash(_METHOD)
        ),
        implementation_hash=_implementation_hash(),
        allowed_utility_fields=list(_DYNAMIC),
    )


@dataclass(frozen=True)
class VerifiedResearchInputAuthorityV1:
    database: object
    witness: ResearchInputAuthorityWitness
    prepared_at: datetime
    valid_until: datetime
    deadline_policy: object | None = None


def _authority_digest(authority):
    body = dict(
        witness=authority.witness.model_dump(mode="json"),
        prepared_at=authority.prepared_at.isoformat(),
        valid_until=authority.valid_until.isoformat(),
    )
    if authority.deadline_policy is not None:
        body["deadline_policy"] = authority.deadline_policy.model_dump(mode="json")
    return canonical_hash(body)


def _issued_deadline_policy(value, prepared_at, valid_until):
    if value is None:
        return None
    from ..ports.perpetual_research_inputs import PerpetualResearchDeadlinePolicyV1
    from .perpetual_research_horizon import derive_perpetual_deadlines

    policy = PerpetualResearchDeadlinePolicyV1.model_validate(value)
    if valid_until != derive_perpetual_deadlines(prepared_at=prepared_at)["candidate_valid_until"]:
        raise ValueError("perpetual_research_deadline_original_mismatch")
    return policy


def issue_manual_input_authority(
    command, *, authenticated_owner, planned_run_id, database, prepared_at, valid_until,
    deadline_policy=None,
):
    command = PerpetualResearchCommandV3.model_validate(command.model_dump(mode="json"))
    at, until = aware_utc(prepared_at), aware_utc(valid_until)
    if (
        not authenticated_owner
        or len(authenticated_owner) > 128
        or authenticated_owner.startswith("system:")
        or not planned_run_id
        or at >= until
    ):
        raise ValueError("perpetual_research_input_authority_invalid")
    witness = ResearchInputAuthorityWitnessV1(
        schema_version="perpetual-research-input-authority.v1",
        origin="manual_v3",
        owner_id=authenticated_owner,
        planned_run_id=planned_run_id,
        request_id=command.request_id,
        command_schema_version=command.schema_version,
        command=command,
        command_hash=canonical_hash(command.model_dump(mode="json")),
        input_policy=command.input_policy,
    )
    return _register_authority(database, witness, at, until, deadline_policy=deadline_policy)


def _register_authority(database, witness, at, until, *, deadline_policy=None):
    policy = _issued_deadline_policy(deadline_policy, at, until)
    authority = VerifiedResearchInputAuthorityV1(database, witness, at, until, policy)
    key = id(authority)
    _AUTHORITIES[key] = (
        weakref.ref(authority, lambda _ref: _AUTHORITIES.pop(key, None)),
        _authority_digest(authority),
        database.engine,
    )
    return authority


def _automatic_identity(proof, consent, *, key, generation, claim_owner):
    return dict(
        source_schema_version=proof["source"]["schema_version"],
        source_proof_hash=canonical_hash(proof),
        consent_schema_version=consent.schema_version,
        consent_ref=consent.revision.model_dump(mode="json"),
        consent_command_hash=consent.command_hash,
        trigger_ref=proof["trigger_ref"],
        claim_request_id=key,
        claim_generation=generation,
        claim_owner=claim_owner,
    )


def _automatic_command_scope(command, proof, consent, owner):
    source = proof["source"]
    if (
        command.request_id != proof["request_id"]
        or owner != source["owner_id"]
        or command.partition.model_dump(mode="json") != source["partition"]
        or command.horizon != source["horizon"]
        or command.input_policy != consent.command.input_policy
        or (
            consent.schema_version in {
                "perpetual-plan-watch-receipt.v2", "perpetual-plan-watch-receipt.v3"
            }
            and (
                command.expected_settings_revision != consent.command.expected_settings_revision
                or command.expected_settings_state_version
                != consent.command.expected_settings_state_version
            )
        )
    ):
        raise ValueError("perpetual_research_automatic_input_scope_mismatch")


def issue_automatic_input_authority(
    command,
    *,
    authenticated_owner,
    planned_run_id,
    database,
    prepared_at,
    valid_until,
    automatic_claim,
    automatic_source,
    deadline_policy=None,
):
    """Issue a database-bound capability only from an exact live durable claim."""
    from .perpetual_research_schedule import (
        read_automatic_source_in_session,
        read_current_paper_consent_in_session,
    )

    command = PerpetualResearchCommandV3.model_validate(command.model_dump(mode="json"))
    at, until = aware_utc(prepared_at), aware_utc(valid_until)
    if (
        not authenticated_owner
        or len(authenticated_owner) > 128
        or authenticated_owner.startswith("system:")
        or not planned_run_id
        or at >= until
        or not isinstance(automatic_claim, tuple)
        or len(automatic_claim) != 3
    ):
        raise ValueError("perpetual_research_input_authority_invalid")
    policy = _issued_deadline_policy(deadline_policy, at, until)
    key, generation, claim_owner = automatic_claim
    with database.session() as session:
        proof = read_automatic_source_in_session(session, key, generation, claim_owner, at=at)
        consent = read_current_paper_consent_in_session(session, proof)
        if (
            proof != automatic_source
            or consent is None
            or (policy is None and until > datetime.fromisoformat(proof["source"]["deadline_at"]))
        ):
            raise ValueError("perpetual_research_current_paper_consent_required")
        _automatic_command_scope(command, proof, consent, authenticated_owner)
    witness = ResearchAutomaticInputAuthorityWitnessV1(
        schema_version="perpetual-research-automatic-input-authority.v1",
        origin="automatic_consent",
        owner_id=authenticated_owner,
        planned_run_id=planned_run_id,
        request_id=command.request_id,
        command_schema_version=command.schema_version,
        command=command,
        command_hash=canonical_hash(command.model_dump(mode="json")),
        input_policy=command.input_policy,
        **_automatic_identity(
            proof, consent, key=key, generation=generation, claim_owner=claim_owner
        ),
    )
    return _register_authority(database, witness, at, until, deadline_policy=policy)


def _verify_automatic_input_authority(session, admission, binding):
    from ..persistence.risk_research_records import RiskResearchRequestRecord
    from .perpetual_research_schedule import (
        read_automatic_source_in_session,
        read_current_paper_consent_in_session,
    )

    witness = binding.authority
    row = session.get(RiskResearchRequestRecord, witness.claim_request_id, populate_existing=True)
    run = session.get(RunRecord, binding.run_id, populate_existing=True)
    if (
        row is None
        or row.lease_generation != witness.claim_generation
        or run is None
        or run.admission_origin != "automatic"
    ):
        raise ValueError("perpetual_research_automatic_input_claim_mismatch")
    # During the atomic installer the caller still owns its live claim. After
    # commit the durable row deliberately clears lease_owner; historical reads
    # authenticate its immutable source and exact Run, never today's lease/head.
    installing = row.status == "claiming" and row.run_id is None
    proof = read_automatic_source_in_session(
        session,
        witness.claim_request_id,
        witness.claim_generation,
        witness.claim_owner,
        at=datetime.fromisoformat(admission["admitted_at"]),
        require_active=False,
        require_claim_live=False,
        run_id=None if installing else binding.run_id,
    )
    consent = read_current_paper_consent_in_session(session, proof)
    if (
        consent is None
        or proof != admission.get("automatic_source")
        or (admission["schema_version"] != "perpetual-research-admission.v3"
            and binding.valid_until != datetime.fromisoformat(proof["source"]["deadline_at"]))
    ):
        raise ValueError("perpetual_research_automatic_input_original_mismatch")
    from .perpetual_research_horizon import authenticate_perpetual_deadlines

    authenticate_perpetual_deadlines(admission)
    _automatic_command_scope(witness.command, proof, consent, binding.owner_id)
    expected = _automatic_identity(
        proof,
        consent,
        key=witness.claim_request_id,
        generation=witness.claim_generation,
        claim_owner=witness.claim_owner,
    )
    if any(witness.model_dump(mode="json")[key] != value for key, value in expected.items()):
        raise ValueError("perpetual_research_automatic_input_original_mismatch")


def _require_authority(authority, session):
    if not isinstance(authority, VerifiedResearchInputAuthorityV1):
        raise ValueError("perpetual_research_input_authority_required")
    issued = _AUTHORITIES.get(id(authority))
    if (
        issued is None
        or issued[0]() is not authority
        or issued[1] != _authority_digest(authority)
        or session.get_bind() is not issued[2]
    ):
        raise ValueError("perpetual_research_input_authority_unissued")
    return authority.witness


def _effective_values(base, account, position):
    body = base.model_dump(mode="json")
    utility = dict(body["utility_policy"])
    utility.update(
        account_ref=account.original.model_dump(mode="json"), position_scenario=position.state
    )
    utility["hash"] = canonical_hash(
        {key: value for key, value in utility.items() if key != "hash"}
    )
    body.update(
        account_snapshot=account.model_dump(mode="json"),
        position_context=position.model_dump(mode="json"),
        utility_policy=utility,
    )
    return DecisionSettingsValuesV1.model_validate(body)


def _effective_refs(run_id, values):
    prefix = _binding_id(run_id)
    return ResearchEffectiveRefsV1(
        account_ref=DecisionSourceRefV1(
            ref=prefix + "#account",
            hash=canonical_hash(values.account_snapshot.model_dump(mode="json")),
        ),
        position_ref=DecisionSourceRefV1(
            ref=prefix + "#position",
            hash=canonical_hash(values.position_context.model_dump(mode="json")),
        ),
        utility_ref=DecisionSourceRefV1(
            ref=values.utility_policy.policy_ref, hash=values.utility_policy.hash
        ),
    )


def prepare_effective_inputs_in_session(session, *, authority, saved, paper_projection):
    witness = _require_authority(authority, session)
    command = witness.command
    partition = DecisionSettingsPartitionV1.model_validate(
        command.partition.model_dump(mode="json")
    )
    current = DecisionSettingsService.current_in_session(session, witness.owner_id, partition)
    if (
        saved is None
        or current != saved
        or saved.revision.operation != "upsert"
        or saved.revision.values is None
        or saved.revision.owner_id != witness.owner_id
        or saved.revision.partition != partition
        or saved.state_version != command.expected_settings_state_version
        or {"ref": saved.revision.revision_id, "hash": saved.revision.payload_hash}
        != command.expected_settings_revision.model_dump(mode="json")
    ):
        raise ValueError("perpetual_research_input_settings_mismatch")
    account, position = paper_projection.account_reader(session, witness.owner_id, partition)
    state = PerpetualPaperRepository(session).current(witness.owner_id, partition.account_id)
    if (
        account.original.model_dump(mode="json") != state["revision"]
        or aware_utc(account.as_of) > authority.prepared_at
    ):
        raise ValueError("perpetual_research_input_ledger_mismatch")
    values = _effective_values(saved.revision.values, account, position)
    binding = ResearchAccountBindingV1(
        schema_version="perpetual-research-account-binding.v1",
        run_id=witness.planned_run_id,
        owner_id=witness.owner_id,
        partition=partition,
        horizon=command.horizon,
        input_policy=INPUT_POLICY,
        authority=witness,
        settings_revision=command.expected_settings_revision,
        settings_state_version=saved.state_version,
        base_utility_ref=DecisionSourceRefV1(
            ref=saved.revision.revision_id + "#utility",
            hash=canonical_hash(saved.revision.values.utility_policy.model_dump(mode="json")),
        ),
        ledger_revision=account.original,
        ledger_state_version=state["state_version"],
        observed_at=account.as_of,
        prepared_at=authority.prepared_at,
        valid_until=authority.valid_until,
        effective_values=values,
        effective_refs=_effective_refs(witness.planned_run_id, values),
        derivation=_derivation(),
    )
    return PreparedResearchAccountInputsV1(binding, _reference(binding), values)


def prepared_admission_body(value):
    if isinstance(value, dict) and set(value) == {"account_binding", "admission"}:
        return PreparedPerpetualAdmissionV2.model_validate(value).admission
    if isinstance(value, dict) and value.get("schema_version") == "perpetual-research-admission.v1":
        return value
    raise ValueError("perpetual_research_prepared_inputs_invalid")


def _read_account_binding(session, *, run_id, reference, committed_at):
    ref = DecisionSourceRefV1.model_validate(reference)
    row = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == ACCOUNT_BINDING_ROLE
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise ValueError("perpetual_research_account_binding_missing")
    binding = ResearchAccountBindingV1.model_validate_json(row.payload_json)
    if (
        row.output_id != _binding_id(run_id)
        or row.schema_version != binding.schema_version
        or binding.run_id != run_id
        or _reference(binding) != ref
        or row.payload_hash != ref.hash
        or _canonical(binding.model_dump(mode="json")) != row.payload_json
        or as_utc(row.created_at) != aware_utc(committed_at)
    ):
        raise ValueError("perpetual_research_account_binding_original_invalid")
    return binding


def save_account_binding_in_session(session, *, binding, commit_at):
    native = session.connection().connection.driver_connection
    if not native.in_transaction:
        raise ValueError("perpetual_research_input_writer_required")
    binding = ResearchAccountBindingV1.model_validate(binding.model_dump(mode="json"))
    ref = _reference(binding)
    old = session.get(RunRoleOutputRecord, ref.ref, populate_existing=True)
    if old is None:
        session.add(
            RunRoleOutputRecord(
                output_id=ref.ref,
                run_id=binding.run_id,
                role=ACCOUNT_BINDING_ROLE,
                schema_version=binding.schema_version,
                payload_json=_canonical(binding.model_dump(mode="json")),
                payload_hash=ref.hash,
                created_at=aware_utc(commit_at),
            )
        )
        session.flush()
    _read_account_binding(session, run_id=binding.run_id, reference=ref, committed_at=commit_at)
    return ref


def _verify_authority_receipt(session, admission, binding):
    witness = binding.authority
    command = witness.command
    key = "perpresearch_" + canonical_hash([binding.owner_id, witness.request_id])
    row = session.get(ProductCommandRecord, key, populate_existing=True)
    if row is None:
        raise ValueError("perpetual_research_input_authority_receipt_missing")
    receipt = PerpetualResearchReceiptV3.model_validate_json(row.result_json)
    body = receipt.model_dump(mode="json")
    if (
        row.kind != "perpetual_research"
        or row.owner_id != binding.owner_id
        or row.target_id != binding.run_id
        or row.payload_hash != witness.command_hash
        or receipt.command_hash != witness.command_hash
        or receipt.request_id != witness.request_id
        or receipt.owner_id != binding.owner_id
        or receipt.run_id != binding.run_id
        or receipt.input_policy != INPUT_POLICY
        or receipt.input_binding_ref != _reference(binding)
        or receipt.horizon != binding.horizon
        or receipt.partition.model_dump(mode="json") != binding.partition.model_dump(mode="json")
        or receipt.settings_revision != binding.settings_revision
        or receipt.settings_revision != command.expected_settings_revision
        or receipt.admission_ref.model_dump(mode="json")
        != {
            "ref": "perp_admission_" + canonical_hash(binding.run_id),
            "hash": canonical_hash(admission),
        }
        or receipt.event_id != admission["event_id"]
        or receipt.study_id != admission["study_id"]
        or receipt.opportunity_id != admission["opportunity_id"]
        or receipt.accepted_at != as_utc(row.created_at)
        or receipt.accepted_at != datetime.fromisoformat(admission["admitted_at"])
        or canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
        != receipt.payload_hash
        or _canonical(body) != row.result_json
    ):
        raise ValueError("perpetual_research_input_authority_receipt_mismatch")


def read_input_binding_identity_in_session(session, admission):
    """SQL-only original pair/command identity; no current head or archive availability."""
    if (
        admission.get("schema_version") not in {
            "perpetual-research-admission.v2", "perpetual-research-admission.v3"
        }
        or admission.get("input_policy") != INPUT_POLICY
    ):
        raise ValueError("perpetual_research_input_version_unsupported")
    binding = _read_account_binding(
        session,
        run_id=admission["run_id"],
        reference=admission["input_binding_ref"],
        committed_at=datetime.fromisoformat(admission["admitted_at"]),
    )
    from .perpetual_research_horizon import authenticate_perpetual_deadlines

    authenticate_perpetual_deadlines(admission)
    if (admission["schema_version"] == "perpetual-research-admission.v3"
            and binding.prepared_at != datetime.fromisoformat(admission["deadline_prepared_at"])):
        raise ValueError("perpetual_research_deadline_original_mismatch")
    witness, command = binding.authority, binding.authority.command
    saved = DecisionSettingsService._revision(session, binding.settings_revision.ref)
    if (
        saved.payload_hash != binding.settings_revision.hash
        or saved.operation != "upsert"
        or saved.values is None
        or saved.model_dump(mode="json") != admission["settings_revision"]
        or saved.revision_no != binding.settings_state_version
        or binding.settings_state_version != admission["settings_state_version"]
        or saved.owner_id != binding.owner_id
        or saved.partition != binding.partition
        or binding.owner_id != admission["owner_id"]
        or binding.partition.model_dump(mode="json") != admission["partition"]
        or binding.horizon != admission["context"]["horizon"]
        or witness.owner_id != binding.owner_id
        or witness.planned_run_id != binding.run_id
        or witness.request_id != command.request_id
        or witness.command_schema_version != command.schema_version
        or witness.command_hash != canonical_hash(command.model_dump(mode="json"))
        or witness.input_policy != command.input_policy
        or binding.input_policy != command.input_policy
        or command.partition.model_dump(mode="json") != binding.partition.model_dump(mode="json")
        or command.horizon != binding.horizon
        or command.expected_settings_revision != binding.settings_revision
        or command.expected_settings_state_version != binding.settings_state_version
        or not binding.observed_at
        <= binding.prepared_at
        <= datetime.fromisoformat(admission["admitted_at"])
        < binding.valid_until
        or binding.valid_until != datetime.fromisoformat(admission["valid_until"])
    ):
        raise ValueError("perpetual_research_input_authority_scope_mismatch")
    if witness.origin == "manual_v3":
        if "automatic_source" in admission:
            raise ValueError("perpetual_research_input_authority_scope_mismatch")
    else:
        _verify_automatic_input_authority(session, admission, binding)
    if binding.effective_values.model_dump(mode="json") != admission["effective_values"] or any(
        admission["context"][key] != value
        for key, value in binding.effective_refs.model_dump(mode="json").items()
    ):
        raise ValueError("perpetual_research_effective_values_mismatch")
    _verify_authority_receipt(session, admission, binding)
    return binding


def read_effective_inputs_in_session(session, admission, *, purpose):
    if purpose not in {"active_research", "frozen_read"}:
        raise ValueError("perpetual_research_input_purpose_invalid")
    version = admission.get("schema_version")
    if version == "perpetual-research-admission.v1":
        if any(
            key in admission for key in ("input_policy", "input_binding_ref", "effective_values")
        ):
            raise ValueError("perpetual_research_input_legacy_policy_forbidden")
        return AuthenticatedResearchInputs(
            "saved",
            None,
            DecisionSettingsValuesV1.model_validate(admission["settings_revision"]["values"]),
        )
    if (
        version not in {"perpetual-research-admission.v2", "perpetual-research-admission.v3"}
        or admission.get("input_policy") != INPUT_POLICY
    ):
        raise ValueError("perpetual_research_input_version_unsupported")
    binding = read_input_binding_identity_in_session(session, admission)
    saved = DecisionSettingsService._revision(session, binding.settings_revision.ref)
    from .perpetual_paper import read_account_revision_in_session

    source = OKXPerpetualOriginalReader(RawBodyArchive(Path(admission["archive_root"])))
    account, position = read_account_revision_in_session(
        session,
        owner=binding.owner_id,
        partition=binding.partition,
        revision_ref=binding.ledger_revision,
        source=source,
    )
    ledger = PerpetualPaperRepository(session).revision(binding.ledger_revision.ref)
    expected = _effective_values(saved.values, account, position)
    metadata = binding.derivation
    if (
        ledger["state_version"] != binding.ledger_state_version
        or account.as_of != binding.observed_at
        or expected != binding.effective_values
        or expected.model_dump(mode="json") != admission["effective_values"]
        or _effective_refs(binding.run_id, expected) != binding.effective_refs
        or binding.base_utility_ref.model_dump(mode="json")
        != {
            "ref": saved.revision_id + "#utility",
            "hash": canonical_hash(saved.values.utility_policy.model_dump(mode="json")),
        }
        or metadata.method_ref.model_dump(mode="json")
        != {"ref": "perpetual-current-paper-input-derivation.v1", "hash": canonical_hash(_METHOD)}
        or metadata.allowed_utility_fields != list(_DYNAMIC)
        or any(
            admission["context"][key] != value
            for key, value in binding.effective_refs.model_dump(mode="json").items()
        )
        or admission["context"]["settings_ref"] != binding.settings_revision.model_dump(mode="json")
    ):
        raise ValueError("perpetual_research_effective_values_mismatch")
    if purpose == "active_research":
        current = DecisionSettingsService.current_in_session(
            session, binding.owner_id, binding.partition
        )
        if (
            current is None
            or current.revision != saved
            or current.state_version != binding.settings_state_version
        ):
            raise ValueError("perpetual_research_settings_head_changed")
        head = PerpetualPaperRepository(session).current(
            binding.owner_id, binding.partition.account_id
        )
        if head["revision"] != binding.ledger_revision.model_dump(mode="json"):
            raise ValueError("perpetual_research_account_original_changed")
        if metadata.implementation_hash != _implementation_hash():
            raise ValueError("perpetual_research_input_implementation_changed")
    # Historical code provenance is part of the receipt-anchored original. It
    # is not overwritten or required to equal today's source-file checksum.
    return AuthenticatedResearchInputs("current_paper", binding, expected)
