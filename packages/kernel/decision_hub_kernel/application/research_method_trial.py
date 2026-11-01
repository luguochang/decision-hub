"""Identity-only engineering trial admission; no financial qualification."""

from dataclasses import dataclass
from packages.kernel.decision_hub_kernel.application.research_learning_async import learning_sync
from hashlib import sha256
from pathlib import Path
from packages.kernel.decision_hub_kernel.ports.research_learning import BehaviorOriginalRef as Ref
from packages.kernel.decision_hub_kernel.application.research_skill_behavior import ref
from packages.kernel.decision_hub_kernel.persistence.research_learning_repository import (
    LearningRepository,
)
from packages.kernel.decision_hub_kernel.decision.research_method_trial import derive_trial_receipt
from packages.kernel.decision_hub_kernel.persistence.research_method_trial import TrialRepository
from packages.kernel.decision_hub_kernel.application.research_skill_package import encoded
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    begin_immediate,
    prepare_originals,
)


@dataclass(frozen=True)
class PreparedTrialAdmissionRead:
    material: object
    protocol: object
    behavior: object
    receipt: object
    pointer: object
    source: object
    originals: object
    source_files: tuple

    def verify(self, session):
        self.source.verify(session)
        self.originals.verify(session)
        self.verify_bytes()

    def verify_after_writes(self, session):
        self.source.verify_after_writes(session)
        self.originals.verify_after_writes(session)
        self.verify_bytes()

    def verify_bytes(self):
        self.material.verify_bytes()
        self.behavior.verify_bytes()
        if any(
            (sha256(path.read_bytes()).hexdigest() != digest for path, digest in self.source_files)
        ):
            raise ValueError("trial_application_source_changed")


class TrialAdmissionReader:
    def __init__(self, database, materials, behavior_reader, source_reader, *, clock):
        if (
            materials.database is not database
            or source_reader.database is not database
            or behavior_reader.store.database is not database
        ):
            raise ValueError("trial_service_database_mismatch")
        self.database, self.materials, self.behavior_reader, self.sources = (
            database,
            materials,
            behavior_reader,
            source_reader,
        )
        self.clock = clock

    def _prepare(
        self, job_id: str, package_receipt_id: str, behavior_receipt_id: str, trial_protocol_id: str
    ):
        if any(
            (
                not isinstance(value, str) or not 0 < len(value) <= 128
                for value in (job_id, package_receipt_id, behavior_receipt_id, trial_protocol_id)
            )
        ):
            raise ValueError("trial_identity_required")
        with self.database.session() as session:
            binding = LearningRepository(session).binding(job_id)
        if binding is None:
            raise ValueError("trial_learning_job_missing")
        source = self.sources.prepare(binding.source_kind, binding.source.ref)
        source_files = tuple(
            (
                (path, sha256(path.read_bytes()).hexdigest())
                for path in Path(__file__).parent.glob("*.py")
            )
        )

        def read(session):
            material = self.materials.read_in_session(session, job_id, package_receipt_id)
            if (
                material.binding != binding
                or source.identity.source != binding.source
                or source.available_at < binding.source_available_at
                or (encoded(source.bodies) != encoded(material.snapshot["originals"]))
            ):
                raise ValueError("trial_authenticated_source_changed")
            repository = TrialRepository(session)
            protocol = repository.read("protocol", trial_protocol_id)
            if protocol is None:
                raise ValueError("trial_protocol_missing")
            if (
                protocol.job_id != job_id
                or protocol.registered_at < material.stage.recorded_at
                or protocol.package_stage
                != Ref(ref=material.stage.id, hash=material.stage.payload_hash)
                or (
                    protocol.parent
                    != Ref(
                        ref=material.parent.manifest.version_id,
                        hash=material.parent.manifest.payload_hash,
                    )
                )
                or (
                    protocol.candidate
                    != Ref(
                        ref=material.candidate.manifest.version_id,
                        hash=material.candidate.manifest.payload_hash,
                    )
                )
                or (
                    (protocol.scope_id, protocol.horizon, protocol.target.model_dump())
                    != (
                        material.learning_protocol.scope_id,
                        material.learning_protocol.horizon,
                        material.learning_protocol.target.model_dump(),
                    )
                )
            ):
                raise ValueError("trial_protocol_package_mismatch")
            behavior = self.behavior_reader.read_in_session(
                session,
                protocol.behavior_protocol.ref,
                behavior_receipt_id,
                material,
                now=self.clock(),
            )
            if (
                ref(behavior.protocol) != protocol.behavior_protocol
                or protocol.registered_at > behavior.protocol.registered_at
            ):
                raise ValueError("trial_behavior_protocol_hash_mismatch")
            old = repository.read("receipt", "method_trial_" + protocol.payload_hash)
            return (material, protocol, behavior, old, repository.pointer(protocol))

        prepared = prepare_originals(self.database, read)
        material, protocol, behavior, old, pointer = prepared.value
        expected = derive_trial_receipt(
            protocol, behavior.receipt, now=old.admitted_at if old else self.clock()
        )
        if old is not None and (old != expected or old.admitted_at > self.clock()):
            raise ValueError("trial_saved_receipt_changed")
        result = PreparedTrialAdmissionRead(
            material, protocol, behavior, old, pointer, source, prepared, source_files
        )
        result.verify_bytes()
        return result

    def prepare_receipt(self, receipt_id: str):
        """Identity-only readonly authority for a future selector's short writer."""
        with self.database.session() as session:
            repository = TrialRepository(session)
            receipt = repository.read("receipt", receipt_id)
            if receipt is None:
                raise ValueError("trial_receipt_missing")
            protocol = repository.read("protocol", receipt.protocol.ref)
        if protocol is None:
            raise ValueError("trial_protocol_missing")
        prepared = self._prepare(
            protocol.job_id, receipt.package_stage.ref, receipt.behavior.ref, protocol.id
        )
        if prepared.receipt != receipt:
            raise ValueError("trial_receipt_changed")
        return prepared

    def read(self, receipt_id: str):
        return self.prepare_receipt(receipt_id).receipt


class TrialAdmissionService(TrialAdmissionReader):
    """Claim-owned writers; a selector only receives TrialAdmissionReader."""

    def __init__(
        self,
        database,
        materials,
        behavior_reader,
        source_reader,
        *,
        clock,
        worker_id: str,
        job_attempt: int,
        behavior_runner=None,
    ):
        super().__init__(database, materials, behavior_reader, source_reader, clock=clock)
        if not worker_id or type(job_attempt) is not int or job_attempt < 1:
            raise ValueError("trial_claim_owner_required")
        self.worker_id, self.job_attempt, self.behavior_runner = (
            worker_id,
            job_attempt,
            behavior_runner,
        )

    def _prepare_evaluation(self, job_id: str, package_receipt_id: str, behavior_protocol_id: str):
        with self.database.session() as session:
            materials = self.materials.read_in_session(session, job_id, package_receipt_id)
            LearningRepository(session).owned(
                job_id, self.worker_id, self.job_attempt, now=self.clock()
            )
            protocol = self.behavior_reader.store.read_in_session(
                session, "protocol", behavior_protocol_id
            )
            if (
                protocol is None
                or protocol.job_id != job_id
                or protocol.package_stage
                != Ref(ref=materials.stage.id, hash=materials.stage.payload_hash)
            ):
                raise ValueError("trial_behavior_identity_mismatch")
        source = self.sources.prepare(materials.binding.source_kind, materials.binding.source.ref)
        if (
            source.identity.source != materials.binding.source
            or source.available_at < materials.binding.source_available_at
            or encoded(source.bodies) != encoded(materials.snapshot["originals"])
        ):
            raise ValueError("trial_authenticated_source_changed")
        if self.behavior_runner is None:
            raise ValueError("trial_behavior_runtime_unavailable")

    async def evaluate(self, job_id: str, package_receipt_id: str, behavior_protocol_id: str):
        await learning_sync(
            self._prepare_evaluation, job_id, package_receipt_id, behavior_protocol_id
        )
        return await self.behavior_runner.evaluate(behavior_protocol_id)

    def admit(
        self, job_id: str, package_receipt_id: str, behavior_receipt_id: str, trial_protocol_id: str
    ):
        prepared = self._prepare(job_id, package_receipt_id, behavior_receipt_id, trial_protocol_id)
        protocol, behavior, pointer = (prepared.protocol, prepared.behavior, prepared.pointer)
        expected_id = "method_trial_" + protocol.payload_hash
        with self.database.session() as session:
            begin_immediate(session)
            repository = TrialRepository(session)
            current = repository.read("receipt", expected_id)
            if current is not None:
                original = derive_trial_receipt(protocol, behavior.receipt, now=current.admitted_at)
                if current != original:
                    raise ValueError("trial_saved_receipt_changed")
                prepared.verify_after_writes(session)
                return current
            prepared.verify(session)
            writer_at = self.clock()
            if pointer is not None:
                pointed = repository.read("receipt", pointer[1])
                active = repository.read("protocol", pointed.protocol.ref)
                if active.end_at > writer_at:
                    raise ValueError("trial_active_trial_conflict")
            LearningRepository(session).owned(
                job_id, self.worker_id, self.job_attempt, now=writer_at
            )
            result = derive_trial_receipt(protocol, behavior.receipt, now=writer_at)
            repository.append("receipt", result)
            prepared.verify_after_writes(session)
            repository.point(protocol, result, pointer)
            return result
