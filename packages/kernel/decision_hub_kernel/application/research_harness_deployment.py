"""Separate Harness deployment CAS; approval does not attest a loaded Run.

Production paired evaluation is intentionally unavailable in this first step.
Only an explicitly isolated SQLite test context can exercise fixture approvals.
No directional table, gate, candidate, or pointer is consumed or modified.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self, cast

from packages.kernel.decision_hub_kernel.application.research_harness_artifact import (
    HarnessArtifactIndexV1,
    HarnessInstalledRuntimeV1,
    InstalledHarnessBaseline,
    ValidatedHarnessArtifact,
    load_harness_artifact,
)
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.persistence.db import Database

Hash = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Id = Annotated[StrictStr, Field(min_length=1, max_length=128)]
MAX_PAIR_BYTES = 1_048_576
STAGES = frozenset({"replay", "holdout", "shadow"})


class HarnessDeploymentError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HarnessCandidateV1(_Closed):
    schema_version: Literal["research-harness-candidate.v1"]
    execution_scope: Literal["research_harness"]
    candidate_id: Id
    version: Id
    domain_pack_ref: Id
    artifact_index: HarnessArtifactIndexV1
    installed_runtime_binding: HarnessInstalledRuntimeV1


class IsolatedPairCaseV1(_Closed):
    case_id: Id
    input_sha256: Hash
    family: Id
    baseline_passed: StrictBool
    candidate_passed: StrictBool
    safety_violations: Annotated[StrictInt, Field(ge=0)]


class IsolatedPairedReportV1(_Closed):
    """A test seam, explicitly never a production evaluation qualification."""

    schema_version: Literal["harness-isolated-paired-report.v1"]
    execution_scope: Literal["research_harness"]
    scorer_version: Literal["harness-isolated-test-scorer.v1"]
    stage: Literal["replay", "holdout", "shadow"]
    candidate: HarnessCandidateV1
    baseline: HarnessCandidateV1
    corpus_hash: Hash
    cases: Annotated[tuple[IsolatedPairCaseV1, ...], Field(min_length=100, max_length=200)]


class HarnessEvaluationReceiptV1(_Closed):
    schema_version: Literal["research-harness-evaluation-receipt.v1"]
    execution_scope: Literal["research_harness"]
    evaluation_id: Id
    verification_basis: Literal["isolated_paired_fixture"]
    stage: Literal["replay", "holdout", "shadow"]
    candidate: HarnessCandidateV1
    baseline: HarnessCandidateV1
    raw_pair_sha256: Hash
    corpus_hash: Hash


class HarnessDecisionRequestV1(_Closed):
    schema_version: Literal["research-harness-decision-request.v1"]
    execution_scope: Literal["research_harness"]
    request_id: Id
    owner: Id
    domain_pack_ref: Id
    candidate: HarnessCandidateV1
    decision: Literal["deploy", "rollback"]
    reason: Annotated[StrictStr, Field(min_length=1, max_length=10000)]
    evaluation_refs: Annotated[tuple[Id, ...], Field(max_length=3)]
    expected_generation: Annotated[StrictInt, Field(ge=0)]


class HarnessDecisionReceiptV1(_Closed):
    schema_version: Literal["research-harness-decision-receipt.v1"]
    execution_scope: Literal["research_harness"]
    request: HarnessDecisionRequestV1
    request_sha256: Hash
    verification_basis: Literal["isolated_paired_fixture"]
    resulting_generation: Annotated[StrictInt, Field(ge=1)]
    previous_candidate: HarnessCandidateV1
    evaluation_refs: tuple[Id, ...]
    state: Literal["approved_for_new_runs"]
    loaded_in_run: Literal[False]
    recorded_at: AwareDatetime


class HarnessPointerV1(_Closed):
    schema_version: Literal["research-harness-pointer.v1"]
    execution_scope: Literal["research_harness"]
    domain_pack_ref: Id
    candidate: HarnessCandidateV1
    generation: Annotated[StrictInt, Field(ge=1)]
    decision_request_id: Id


class HarnessSelectionV1(_Closed):
    schema_version: Literal["research-harness-selection.v1"]
    execution_scope: Literal["research_harness"]
    selection_kind: Literal["release_baseline", "deployed"]
    domain_pack_ref: Id
    expected_generation: Annotated[StrictInt, Field(ge=0)]
    candidate: HarnessCandidateV1
    pointer: HarnessPointerV1 | None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.candidate.domain_pack_ref != self.domain_pack_ref:
            raise HarnessDeploymentError("harness_selection_domain_mismatch")
        if self.selection_kind == "release_baseline":
            if self.expected_generation != 0 or self.pointer is not None:
                raise HarnessDeploymentError("harness_release_selection_invalid")
        elif (
            self.pointer is None or self.expected_generation < 1
            or self.pointer.domain_pack_ref != self.domain_pack_ref
            or self.pointer.generation != self.expected_generation
            or self.pointer.candidate != self.candidate
        ):
            raise HarnessDeploymentError("harness_deployed_selection_invalid")
        return self


class HarnessPrincipalAuthorizer(Protocol):
    """Trusted server authentication adapter, not a request-header wrapper.

    The application must validate its real session/credential and owner
    permissions for this opaque principal. No production adapter exists yet;
    leaving this unset closes all owner mutations before any transaction.
    """

    def require_owner(
        self, principal: object, *, owner_identity: str, domain_pack_ref: str,
        operation: Literal["register", "evaluate", "deploy", "rollback"],
    ) -> None: ...


# Dedicated tables, never Evolution's directional pointer. Future promotion
# needs an explicit Alembic migration; constructing this service creates none.
HARNESS_METADATA = MetaData()


def _receipt_table(name: str, key: str) -> Table:
    return Table(
        name,
        HARNESS_METADATA,
        Column(key, String(128), primary_key=True),
        Column("execution_scope", String(32), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("payload_hash", String(64), nullable=False),
        CheckConstraint("execution_scope = 'research_harness'"),
    )


CANDIDATES = _receipt_table("research_harness_candidates", "candidate_id")
EVALUATIONS = _receipt_table("research_harness_evaluations", "evaluation_id")
DECISIONS = _receipt_table("research_harness_deployment_decisions", "request_id")
POINTERS = Table(
    "research_harness_pointers",
    HARNESS_METADATA,
    Column("domain_pack_ref", String(128), primary_key=True),
    Column("execution_scope", String(32), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    CheckConstraint("execution_scope = 'research_harness'"),
    CheckConstraint("generation >= 1"),
)


def _encoded(value: BaseModel) -> str:
    return canonical_recipe(value.model_dump(mode="json"))


def _hash(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _read[T: BaseModel](
    session: Session, table: Table, key: str, identity: str, model: type[T]
) -> T | None:
    row = session.execute(select(table).where(table.c[key] == identity)).mappings().one_or_none()
    if row is None:
        return None
    raw = row["payload_json"]
    if row["execution_scope"] != "research_harness" or _hash(raw) != row["payload_hash"]:
        raise HarnessDeploymentError("harness_original_integrity_invalid")
    result = model.model_validate_json(raw)
    body = result.model_dump(mode="json")
    # Every primary-key lookup is tied back to the original closed payload.
    original_identity = body.get(key)
    if table is DECISIONS:
        original_identity = body["request"][key]
    if original_identity != identity:
        raise HarnessDeploymentError("harness_original_identity_invalid")
    if table is POINTERS and body["generation"] != row["generation"]:
        raise HarnessDeploymentError("harness_pointer_integrity_invalid")
    if table is DECISIONS and (
        body["request_sha256"] != _hash(canonical_recipe(body["request"]))
        or body["resulting_generation"] != body["request"]["expected_generation"] + 1
    ):
        raise HarnessDeploymentError("harness_decision_integrity_invalid")
    return result


def _append(session: Session, table: Table, key: str, identity: str, value: BaseModel) -> None:
    raw = _encoded(value)
    session.execute(
        insert(table).values(
            {
                key: identity,
                "execution_scope": "research_harness",
                "payload_json": raw,
                "payload_hash": _hash(raw),
            }
        )
    )


@dataclass(frozen=True)
class PreparedHarnessChoice:
    selection_json: bytes
    decision_json: bytes | None
    artifact: ValidatedHarnessArtifact

    @property
    def selection(self) -> HarnessSelectionV1:
        return HarnessSelectionV1.model_validate_json(self.selection_json)

    @property
    def pointer(self) -> HarnessPointerV1 | None:
        return self.selection.pointer


class ResearchHarnessDeploymentService:
    def __init__(
        self,
        database: Database,
        *,
        archive_root: Path,
        baseline: InstalledHarnessBaseline,
        release_baseline_index: HarnessArtifactIndexV1,
        owner_identity: str,
        principal_authorizer: HarnessPrincipalAuthorizer | None = None,
        isolated_fixture_root: Path | None = None,
    ) -> None:
        self.database = database
        self.archive_root = archive_root.resolve(strict=True)
        self.baseline = InstalledHarnessBaseline(baseline.definition_json, baseline.runtime_json)
        self.owner_identity = owner_identity
        self.principal_authorizer = principal_authorizer
        if not owner_identity:
            raise HarnessDeploymentError("owner_identity_required")
        self.isolated = isolated_fixture_root is not None
        if self.isolated:
            assert isolated_fixture_root is not None
            self._verify_fixture_database(isolated_fixture_root)
        release = load_harness_artifact(
            self.archive_root, release_baseline_index, baseline=self.baseline
        )
        if _encoded(release.definition).encode("utf-8") != self.baseline.definition_json:
            raise HarnessDeploymentError("harness_release_baseline_mismatch")
        self.release = self._ref(release)

    @staticmethod
    def _ref(artifact: ValidatedHarnessArtifact) -> HarnessCandidateV1:
        # Artifact has been fully validated against the trusted installed baseline.
        import json

        value = json.loads(artifact.raw_bytes)
        return HarnessCandidateV1(
            schema_version="research-harness-candidate.v1",
            execution_scope="research_harness",
            candidate_id=value["candidate_id"],
            version=value["version"],
            domain_pack_ref=value["domain_pack_ref"],
            artifact_index=artifact.index,
            installed_runtime_binding=HarnessInstalledRuntimeV1.model_validate(
                value["installed_runtime_binding"]
            ),
        )

    def _verify_fixture_database(self, fixture_root: Path) -> None:
        """Fixture qualification cannot be enabled from a promoted source.

        Only this artifact's managed isolated namespace and exact layout are
        valid. Broad caller ancestors, aliases and hard links are never proof
        that a database is isolated from the running product.
        """
        source = Path(__file__).resolve(strict=True)
        suffix = Path(
            "artifacts/ar10-harness-deployment-service02/proposed/packages/kernel/"
            "decision_hub_kernel/application/research_harness_deployment.py"
        )
        repository = next((parent for parent in source.parents if parent / suffix == source), None)
        if repository is None:
            raise HarnessDeploymentError("harness_fixture_stage_only")
        namespace = repository / "artifacts/ar10-harness-deployment-service02/isolated"
        namespace_real = namespace.resolve(strict=True)
        root = fixture_root.resolve(strict=True)
        db_path = self.database.engine.url.database
        if (
            namespace_real != namespace.absolute()
            or not root.is_relative_to(namespace_real) or root == namespace_real
            or self.database.engine.dialect.name != "sqlite" or not db_path
            or db_path == ":memory:"
        ):
            raise HarnessDeploymentError("harness_fixture_database_outside_root")
        expected_db = root / "isolated.sqlite3"
        actual_db = Path(db_path).resolve(strict=True)
        if (
            actual_db != expected_db or self.archive_root != root / "archive"
            or not actual_db.is_file() or actual_db.stat().st_nlink != 1
        ):
            raise HarnessDeploymentError("harness_fixture_database_outside_root")
        with self.database.engine.connect() as connection:
            attached = connection.exec_driver_sql("PRAGMA database_list").all()
            main = [row for row in attached if row[1] == "main"]
            if (
                len(main) != 1 or Path(main[0][2]).resolve(strict=True) != expected_db
                or any(row[1] != "main" and (row[1] != "temp" or row[2] != "") for row in attached)
                or connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM temp.sqlite_master WHERE type = 'table'"
                ).scalar_one() != 0
            ):
                raise HarnessDeploymentError("harness_fixture_physical_database_mismatch")
            tables = set(connection.exec_driver_sql(
                "SELECT name FROM main.sqlite_master WHERE type = 'table'"
            ).scalars())
            if tables != {table.name for table in HARNESS_METADATA.tables.values()}:
                raise HarnessDeploymentError("harness_fixture_database_schema_not_isolated")

    def _owner(
        self, principal: object, declared_owner: str, *, domain_pack_ref: str,
        operation: Literal["register", "evaluate", "deploy", "rollback"],
    ) -> None:
        # The existing API require_owner returns an unauthenticated label.
        # It cannot be used directly or wrapped as a serialized principal here.
        if principal is None or isinstance(principal, (str, bytes, dict, list, tuple, BaseModel)):
            raise PermissionError("harness_authenticated_principal_required")
        if self.principal_authorizer is None:
            raise PermissionError("harness_principal_authentication_unavailable")
        self.principal_authorizer.require_owner(
            principal, owner_identity=self.owner_identity, domain_pack_ref=domain_pack_ref,
            operation=operation,
        )
        if declared_owner != self.owner_identity:
            raise PermissionError("owner_identity_mismatch")

    @staticmethod
    def _begin(session: Session) -> None:
        if session.get_bind().dialect.name != "sqlite":
            raise HarnessDeploymentError("harness_sqlite_required")
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    def _artifact(self, candidate: HarnessCandidateV1) -> ValidatedHarnessArtifact:
        artifact = load_harness_artifact(
            self.archive_root, candidate.artifact_index, baseline=self.baseline
        )
        if self._ref(artifact) != candidate:
            raise HarnessDeploymentError("harness_candidate_artifact_mismatch")
        return artifact

    def _fresh_bytes(self, address: str, expected_hash: str, maximum: int) -> bytes:
        path = (self.archive_root / address).resolve(strict=True)
        if not path.is_relative_to(self.archive_root) or not path.is_file():
            raise HarnessDeploymentError("harness_original_outside_archive")
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
        if (
            not raw
            or len(raw) > maximum
            or _hash(raw) != expected_hash
            or (self.archive_root / address).resolve(strict=True) != path
        ):
            raise HarnessDeploymentError("harness_original_bytes_changed")
        return raw

    def register_candidate(
        self, candidate: HarnessCandidateV1, *, authorized_principal: object
    ) -> HarnessCandidateV1:
        candidate = HarnessCandidateV1.model_validate_json(_encoded(candidate))
        self._owner(
            authorized_principal, self.owner_identity,
            domain_pack_ref=candidate.domain_pack_ref, operation="register",
        )
        artifact = self._artifact(candidate)
        with self.database.session() as session:
            self._begin(session)
            prior = _read(
                session, CANDIDATES, "candidate_id", candidate.candidate_id, HarnessCandidateV1
            )
            if prior is not None:
                if prior != candidate:
                    raise HarnessDeploymentError("harness_candidate_request_reused")
                return prior
            self._fresh_bytes(
                candidate.artifact_index.content_address,
                candidate.artifact_index.artifact_bytes_sha256,
                len(artifact.raw_bytes),
            )
            _append(session, CANDIDATES, "candidate_id", candidate.candidate_id, candidate)
        return candidate

    def _pair(self, receipt: HarnessEvaluationReceiptV1) -> IsolatedPairedReportV1:
        raw = self._fresh_bytes(
            f"{receipt.raw_pair_sha256}.pair.json", receipt.raw_pair_sha256, MAX_PAIR_BYTES
        )
        pair = IsolatedPairedReportV1.model_validate_json(raw)
        if (
            pair.candidate != receipt.candidate
            or pair.baseline != receipt.baseline
            or pair.stage != receipt.stage
            or pair.corpus_hash != receipt.corpus_hash
        ):
            raise HarnessDeploymentError("harness_paired_evaluation_binding_invalid")
        ids = [item.case_id for item in pair.cases]
        if len(ids) != len(set(ids)) or any(
            not item.baseline_passed or not item.candidate_passed or item.safety_violations
            for item in pair.cases
        ):
            raise HarnessDeploymentError("harness_paired_evaluation_not_qualified")
        corpus = [
            {"case_id": item.case_id, "input_sha256": item.input_sha256, "family": item.family}
            for item in sorted(pair.cases, key=lambda item: item.case_id)
        ]
        if _hash(canonical_recipe(corpus)) != pair.corpus_hash:
            raise HarnessDeploymentError("harness_paired_corpus_hash_invalid")
        families: dict[str, int] = {}
        for case in pair.cases:
            families[case.family] = families.get(case.family, 0) + 1
        if any(count < 30 for count in families.values()):
            raise HarnessDeploymentError("harness_paired_family_coverage_missing")
        return pair

    def record_isolated_evaluation(
        self, receipt: HarnessEvaluationReceiptV1, *, authorized_principal: object
    ) -> None:
        self._owner(
            authorized_principal, self.owner_identity,
            domain_pack_ref=receipt.candidate.domain_pack_ref, operation="evaluate",
        )
        if not self.isolated:
            raise PermissionError("harness_paired_evaluation_gate_unavailable")
        receipt = HarnessEvaluationReceiptV1.model_validate_json(_encoded(receipt))
        self._artifact(receipt.candidate)
        self._artifact(receipt.baseline)
        self._pair(receipt)
        with self.database.session() as session:
            self._begin(session)
            candidate = _read(
                session,
                CANDIDATES,
                "candidate_id",
                receipt.candidate.candidate_id,
                HarnessCandidateV1,
            )
            if candidate != receipt.candidate:
                raise HarnessDeploymentError("harness_evaluation_candidate_not_registered")
            prior = _read(
                session,
                EVALUATIONS,
                "evaluation_id",
                receipt.evaluation_id,
                HarnessEvaluationReceiptV1,
            )
            if prior is not None:
                if prior != receipt:
                    raise HarnessDeploymentError("harness_evaluation_request_reused")
                return
            self._fresh_bytes(
                f"{receipt.raw_pair_sha256}.pair.json", receipt.raw_pair_sha256, MAX_PAIR_BYTES
            )
            _append(session, EVALUATIONS, "evaluation_id", receipt.evaluation_id, receipt)

    @staticmethod
    def _matching_replay(
        prior: HarnessDecisionReceiptV1, request: HarnessDecisionRequestV1
    ) -> HarnessDecisionReceiptV1:
        if prior.request != request or prior.request_sha256 != _hash(_encoded(request)):
            raise HarnessDeploymentError("harness_decision_request_reused")
        return prior

    def decide(
        self, request: HarnessDecisionRequestV1, *, authorized_principal: object
    ) -> HarnessDecisionReceiptV1:
        request = HarnessDecisionRequestV1.model_validate_json(_encoded(request))
        self._owner(
            authorized_principal, request.owner, domain_pack_ref=request.domain_pack_ref,
            operation=request.decision,
        )
        if request.domain_pack_ref != request.candidate.domain_pack_ref:
            raise HarnessDeploymentError("harness_decision_domain_mismatch")
        if not self.isolated:
            raise PermissionError("harness_paired_evaluation_gate_unavailable")
        with self.database.session() as session:
            prior = _read(
                session, DECISIONS, "request_id", request.request_id, HarnessDecisionReceiptV1
            )
            if prior is not None:
                return self._matching_replay(prior, request)
            candidate = _read(
                session,
                CANDIDATES,
                "candidate_id",
                request.candidate.candidate_id,
                HarnessCandidateV1,
            )
            pointer = _read(
                session, POINTERS, "domain_pack_ref", request.domain_pack_ref, HarnessPointerV1
            )
            if candidate != request.candidate:
                raise HarnessDeploymentError("harness_candidate_not_registered")
            generation = pointer.generation if pointer else 0
            if generation != request.expected_generation:
                raise HarnessDeploymentError("harness_pointer_generation_conflict")
            previous = pointer.candidate if pointer else self.release
            if previous == candidate and pointer is not None:
                raise HarnessDeploymentError("harness_candidate_already_active")
            evaluations: list[HarnessEvaluationReceiptV1] = []
            historical: HarnessDecisionReceiptV1 | None = None
            if request.decision == "deploy":
                if len(request.evaluation_refs) != 3 or len(set(request.evaluation_refs)) != 3:
                    raise HarnessDeploymentError("harness_paired_evaluation_refs_invalid")
                for identity in request.evaluation_refs:
                    receipt = _read(
                        session, EVALUATIONS, "evaluation_id", identity, HarnessEvaluationReceiptV1
                    )
                    if (
                        receipt is None
                        or receipt.candidate != candidate
                        or receipt.baseline != previous
                    ):
                        raise HarnessDeploymentError("harness_paired_evaluation_binding_invalid")
                    evaluations.append(receipt)
                if {r.stage for r in evaluations} != STAGES:
                    raise HarnessDeploymentError("harness_paired_stage_coverage_missing")
            else:
                if pointer is None or request.evaluation_refs:
                    raise HarnessDeploymentError("harness_rollback_request_invalid")
                rows = session.execute(select(DECISIONS.c.request_id)).scalars().all()
                for identity in rows:
                    item = _read(
                        session, DECISIONS, "request_id", identity, HarnessDecisionReceiptV1
                    )
                    if (
                        item is not None
                        and item.request.domain_pack_ref == request.domain_pack_ref
                        and (
                            item.request.candidate == candidate
                            or (
                                candidate == self.release
                                and item.request.expected_generation == 0
                                and item.resulting_generation == 1
                                and item.previous_candidate == self.release
                            )
                        )
                    ):
                        historical = item
                        break
                if historical is None:
                    raise HarnessDeploymentError("harness_rollback_target_not_verified")
        artifact = self._artifact(candidate)
        previous_artifact = self._artifact(previous)
        case_ids: set[str] = set()
        for receipt in evaluations:
            pair = self._pair(receipt)
            incoming = {case.case_id for case in pair.cases}
            if case_ids.intersection(incoming):
                raise HarnessDeploymentError("harness_paired_splits_overlap")
            case_ids.update(incoming)
        # Native SQLite writer only rechecks originals/CAS and bounded raw bytes.
        with self.database.session() as session:
            self._begin(session)
            prior = _read(
                session, DECISIONS, "request_id", request.request_id, HarnessDecisionReceiptV1
            )
            if prior is not None:
                return self._matching_replay(prior, request)
            fresh = _read(
                session, POINTERS, "domain_pack_ref", request.domain_pack_ref, HarnessPointerV1
            )
            if fresh != pointer:
                raise HarnessDeploymentError("harness_pointer_generation_conflict")
            current = _read(
                session, CANDIDATES, "candidate_id", candidate.candidate_id, HarnessCandidateV1
            )
            if current != candidate:
                raise HarnessDeploymentError("harness_originals_changed")
            for receipt in evaluations:
                current_eval = _read(
                    session,
                    EVALUATIONS,
                    "evaluation_id",
                    receipt.evaluation_id,
                    HarnessEvaluationReceiptV1,
                )
                if current_eval != receipt:
                    raise HarnessDeploymentError("harness_originals_changed")
                self._fresh_bytes(
                    f"{receipt.raw_pair_sha256}.pair.json", receipt.raw_pair_sha256, MAX_PAIR_BYTES
                )
            if (
                historical is not None
                and _read(
                    session,
                    DECISIONS,
                    "request_id",
                    historical.request.request_id,
                    HarnessDecisionReceiptV1,
                )
                != historical
            ):
                raise HarnessDeploymentError("harness_rollback_history_changed")
            self._fresh_bytes(
                candidate.artifact_index.content_address,
                candidate.artifact_index.artifact_bytes_sha256,
                len(artifact.raw_bytes),
            )
            if pointer is None:
                # The first approved transition records its trusted canonical
                # release in the same transaction. Metadata alone does not grant
                # approval; the candidate's paired gates above still apply.
                registered_release = _read(
                    session, CANDIDATES, "candidate_id", self.release.candidate_id, HarnessCandidateV1,
                )
                if registered_release is not None and registered_release != self.release:
                    raise HarnessDeploymentError("harness_release_baseline_registration_conflict")
                self._fresh_bytes(
                    self.release.artifact_index.content_address,
                    self.release.artifact_index.artifact_bytes_sha256, len(previous_artifact.raw_bytes),
                )
                if registered_release is None:
                    _append(session, CANDIDATES, "candidate_id", self.release.candidate_id, self.release)
            pointer_after = HarnessPointerV1(
                schema_version="research-harness-pointer.v1",
                execution_scope="research_harness",
                domain_pack_ref=request.domain_pack_ref,
                candidate=candidate,
                generation=generation + 1,
                decision_request_id=request.request_id,
            )
            raw = _encoded(pointer_after)
            values = dict(
                execution_scope="research_harness",
                generation=generation + 1,
                payload_json=raw,
                payload_hash=_hash(raw),
            )
            if pointer is None:
                session.execute(
                    insert(POINTERS).values(domain_pack_ref=request.domain_pack_ref, **values)
                )
            else:
                changed = session.execute(
                    update(POINTERS)
                    .where(
                        POINTERS.c.domain_pack_ref == request.domain_pack_ref,
                        POINTERS.c.execution_scope == "research_harness",
                        POINTERS.c.generation == generation,
                    )
                    .values(**values)
                )
                if cast(CursorResult[Any], changed).rowcount != 1:
                    raise HarnessDeploymentError("harness_pointer_generation_conflict")
            decision = HarnessDecisionReceiptV1(
                schema_version="research-harness-decision-receipt.v1",
                execution_scope="research_harness",
                request=request,
                request_sha256=_hash(_encoded(request)),
                verification_basis="isolated_paired_fixture",
                resulting_generation=generation + 1,
                previous_candidate=previous,
                evaluation_refs=tuple(r.evaluation_id for r in evaluations)
                if historical is None
                else historical.evaluation_refs,
                state="approved_for_new_runs",
                loaded_in_run=False,
                recorded_at=datetime.now(UTC),
            )
            _append(session, DECISIONS, "request_id", request.request_id, decision)
        return decision

    def prepare_active_choice(self, domain_pack_ref: str) -> PreparedHarnessChoice:
        if domain_pack_ref != self.release.domain_pack_ref:
            raise HarnessDeploymentError("harness_selection_domain_mismatch")
        with self.database.session() as session:
            pointer = _read(session, POINTERS, "domain_pack_ref", domain_pack_ref, HarnessPointerV1)
            if pointer is None:
                artifact = self._artifact(self.release)
                selection = HarnessSelectionV1(
                    schema_version="research-harness-selection.v1",
                    execution_scope="research_harness", selection_kind="release_baseline",
                    domain_pack_ref=domain_pack_ref, expected_generation=0,
                    candidate=self.release, pointer=None,
                )
                return PreparedHarnessChoice(_encoded(selection).encode("utf-8"), None, artifact)
            if not self.isolated:
                raise PermissionError("harness_paired_evaluation_gate_unavailable")
            decision = _read(
                session,
                DECISIONS,
                "request_id",
                pointer.decision_request_id,
                HarnessDecisionReceiptV1,
            )
            candidate = _read(
                session,
                CANDIDATES,
                "candidate_id",
                pointer.candidate.candidate_id,
                HarnessCandidateV1,
            )
            if (
                decision is None
                or candidate != pointer.candidate
                or decision.request.candidate != candidate
                or decision.resulting_generation != pointer.generation
                or decision.request.domain_pack_ref != domain_pack_ref
            ):
                raise HarnessDeploymentError("harness_pointer_originals_invalid")
        artifact = self._artifact(pointer.candidate)
        selection = HarnessSelectionV1(
            schema_version="research-harness-selection.v1",
            execution_scope="research_harness", selection_kind="deployed",
            domain_pack_ref=domain_pack_ref, expected_generation=pointer.generation,
            candidate=pointer.candidate, pointer=pointer,
        )
        return PreparedHarnessChoice(
            _encoded(selection).encode("utf-8"), _encoded(decision).encode("utf-8"), artifact
        )

    def verify_choice_in_first_freeze(
        self, session: Session, choice: PreparedHarnessChoice
    ) -> None:
        """Caller holds the existing Task/Pack/Compiled first-freeze writer UoW."""
        selection = choice.selection
        if selection.selection_kind == "deployed" and not self.isolated:
            raise PermissionError("harness_paired_evaluation_gate_unavailable")
        connection = session.connection()
        native = connection.connection.driver_connection
        # SQLite exposes physical transaction presence, not its BEGIN mode.
        # Factory must take its existing BEGIN IMMEDIATE before first input read.
        if (
            session.get_bind() is not self.database.engine
            or connection.dialect.name != "sqlite"
            or not isinstance(native, sqlite3.Connection)
            or not native.in_transaction
        ):
            raise HarnessDeploymentError("harness_first_freeze_caller_transaction_required")
        expected = choice.pointer
        current = _read(
            session, POINTERS, "domain_pack_ref", selection.domain_pack_ref, HarnessPointerV1
        )
        if expected is None:
            if (
                current is not None or selection.candidate != self.release
                or choice.decision_json is not None or selection.expected_generation != 0
                or choice.artifact.index != self.release.artifact_index
                or _hash(choice.artifact.raw_bytes) != self.release.artifact_index.artifact_bytes_sha256
                or selection.candidate.installed_runtime_binding != self.baseline.runtime
            ):
                raise HarnessDeploymentError("harness_first_freeze_pointer_changed")
            self._fresh_bytes(
                self.release.artifact_index.content_address,
                self.release.artifact_index.artifact_bytes_sha256, len(choice.artifact.raw_bytes),
            )
            return
        decision = _read(
            session, DECISIONS, "request_id", expected.decision_request_id, HarnessDecisionReceiptV1
        )
        candidate = _read(
            session, CANDIDATES, "candidate_id", expected.candidate.candidate_id, HarnessCandidateV1
        )
        if (
            current != expected
            or decision is None
            or _encoded(decision).encode("utf-8") != choice.decision_json
            or candidate != expected.candidate
            or choice.artifact.index != expected.candidate.artifact_index
            or _hash(choice.artifact.raw_bytes)
            != expected.candidate.artifact_index.artifact_bytes_sha256
            or expected.candidate.installed_runtime_binding != self.baseline.runtime
        ):
            raise HarnessDeploymentError("harness_first_freeze_pointer_changed")
        self._fresh_bytes(
            expected.candidate.artifact_index.content_address,
            expected.candidate.artifact_index.artifact_bytes_sha256, len(choice.artifact.raw_bytes),
        )
