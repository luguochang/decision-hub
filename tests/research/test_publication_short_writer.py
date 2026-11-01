"""Genuine offline Graph originals and actual two-connection writer controls.

One real Factory/typed MCP/Runtime/Graph seed is copied for each case. Only
timing is injected; authentication, readset replay, fences and CAS stay real.
No Provider, HTTP, original-data rewrite or permission verdict is mocked.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    DshSessionSubmit,
    EvidenceCandidate,
    FactEnvelope,
    ResearchSessionResult,
)
from packages.evals.analytical_assessment import assess_outlook_in_session
from packages.kernel.decision_hub_kernel.application import (
    commit as commit_module,
)
from packages.kernel.decision_hub_kernel.application import (
    model_dispatch,
)
from packages.kernel.decision_hub_kernel.application import (
    research_graph_binding as binding,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    Database,
    DshSessionLinkRecord,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    ServiceHeartbeatRecord,
    utcnow,
)
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_model_dispatch_v2 import (
    _payload,  # pyright: ignore[reportPrivateUsage]
    inputs,
)
from tests.orchestration.test_graph_observed_result_v2 import OWNER
from tests.research import test_graph_citation_membership_v2 as citation_fixture
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_graph_citation_membership_v2 import (
    SPOT_A,
    SnapshotCitationHost,
    _snapshot_final,  # pyright: ignore[reportPrivateUsage]
    snapshot_inputs,
)
from tests.research.test_graph_citation_publication_v2 import (
    _renew_draft_lease,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_research_publication_consumer_v2 import (
    _sql,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


class _Producer(Protocol):
    __wrapped__: Callable[[Path], Generator[PreparedInputs]]


class _Network(Protocol):
    __wrapped__: Callable[[pytest.MonkeyPatch], None]


@dataclass(frozen=True)
class Final:
    source: PreparedInputs
    reference: binding.GraphFinalProjectionRef
    result: ResearchSessionResult
    reviewed: ResearchSessionResult


class _AdmittedSnapshotHost(SnapshotCitationHost):
    """Offline MCP body uses real admission before native mapping, like Gateway.

    The original helper predates early accepted-only synthesis validation and
    only admits its candidate body later in Graph. Here the actual Evidence
    service computes disposition before this first original body is returned.
    No stored transport, original hash or authority result is rewritten.
    """

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        response = await super().result(payload)
        events = json.loads(response.events_json)
        body = events[1]["payload"]["event"]["data"]["message"]["content"][0][
            "structuredContent"
        ]
        candidates = [EvidenceCandidate.model_validate(item)
                      for item in body["evidence_candidates"]]
        facts = [FactEnvelope.model_validate(item) for item in body["facts"]]
        accepted = ResearchEvidenceService(self.fixture.database).accept_candidates(
            run_id=self.fixture.request.run_id, capability_id="controlled-native-mcp",
            candidates=candidates, facts=facts,
            requirements={item.requirement_id: item
                          for item in self.fixture.request.evidence_requirements},
            cutoff_at=self.fixture.request.pit_cutoff_at,
        )
        assert {item.quality for item in accepted} == {"accepted", "candidate"}
        body["evidence_candidates"] = [item.model_dump(mode="json") for item in accepted]
        encoded = json.dumps(events)
        return response.model_copy(update={
            "events_json": encoded,
            "result_hash": hashlib.sha256(
                (response.final_response + "\0" + encoded).encode()
            ).hexdigest(),
        })


@pytest.fixture(scope="module")
async def original(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Final]:
    network = pytest.MonkeyPatch()
    cast(_Network, forbid_external_connections).__wrapped__(network)
    directory = tmp_path_factory.mktemp("one-real-graph")
    producer = cast(_Producer, snapshot_inputs).__wrapped__(directory)
    source = next(producer)
    try:
        with pytest.MonkeyPatch.context() as parent:
            parent.setattr(citation_fixture, "SnapshotCitationHost", _AdmittedSnapshotHost)
            result = await _snapshot_final(source, directory, top_ref=SPOT_A)
        _renew_draft_lease(source)
        reference = binding.parse_graph_final_ref(json.loads(
            (directory / "snapshot-citation-original.json").read_text()
        )["graph_final_ref"])
        review = ResearchReviewService(source.database).review(
            source.request.run_id, result,
            graph_final_ref=reference, expected_lease_owner=OWNER,
        )
        yield Final(source, reference, result, result.model_copy(update={"reviews": [review]}))
    finally:
        producer.close()
        network.undo()


@pytest.fixture
def final(original: Final, tmp_path: Path) -> Generator[Final]:
    _renew_draft_lease(original.source)
    path = original.source.database.sqlite_path
    assert path is not None
    destination = tmp_path / "actual-copy.sqlite3"
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as reader:
        with sqlite3.connect(destination) as writer:
            reader.backup(writer)
    database = Database(f"sqlite+pysqlite:///{destination.as_posix()}")
    try:
        yield replace(original, source=replace(original.source, database=database))
    finally:
        database.engine.dispose()


def _publish(final: Final, result: ResearchSessionResult | None = None) -> str:
    artifact, _ = CommitDecisionService(final.source.database).commit_research_result(
        final.source.request.run_id, final.source.request.event_id, result or final.reviewed,
        graph_final_ref=final.reference, expected_lease_owner=OWNER,
    )
    return artifact


def _heartbeat(database: Database) -> None:
    now = utcnow()
    with database.session() as session:
        preparation.begin_immediate(session)
        session.add(ServiceHeartbeatRecord(
            service_id="independent-writer", role="research_worker", instance_id="other",
            version="test", mode="controlled", interval_seconds=1, started_at=now,
            heartbeat_at=now,
        ))


@pytest.mark.parametrize("consumer", ["review", "commit"])
def test_full_graph_authentication_allows_another_actual_writer(
    final: Final, monkeypatch: pytest.MonkeyPatch, consumer: str,
) -> None:
    entered, release = threading.Event(), threading.Event()
    reader = binding.read_graph_final_in_session
    calls: list[bool] = []

    def controlled(session: Session, **kwargs: Any) -> binding.FrozenGraphFinal:
        native = session.connection().connection.driver_connection
        assert isinstance(native, sqlite3.Connection) and native.in_transaction
        calls.append(kwargs["require_current_permission"])
        entered.set()
        assert release.wait(12)
        return reader(session, **kwargs)

    monkeypatch.setattr(binding, "read_graph_final_in_session", controlled)

    def consume() -> object:
        if consumer == "review":
            return ResearchReviewService(final.source.database).review(
                final.source.request.run_id, final.result,
                graph_final_ref=final.reference, expected_lease_owner=OWNER,
            )
        return _publish(final)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(consume)
        try:
            assert entered.wait(30)
            started = time.monotonic()
            _heartbeat(final.source.database)
            assert time.monotonic() - started < 3
        finally:
            release.set()
        assert task.result(timeout=90) is not None
    assert calls == [False], "Complete Graph proof occurs once, in the read snapshot"
    with final.source.database.session() as session:
        assert session.get(ServiceHeartbeatRecord, "independent-writer") is not None


def test_native_model_authentication_allows_another_actual_writer(
    inputs: PreparedInputs, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    entered, release = threading.Event(), threading.Event()
    reader = model_dispatch.read_prepared_execution_in_session

    def controlled(session: Session, run_id: str) -> Any:
        entered.set()
        assert release.wait(12)
        return reader(session, run_id)

    monkeypatch.setattr(model_dispatch, "read_prepared_execution_in_session", controlled)
    payload = _payload(inputs)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(model_dispatch.ModelDispatchService(inputs.database).bind, payload)
        try:
            assert entered.wait(30)
            _heartbeat(inputs.database)
        finally:
            release.set()
        assert task.result(timeout=45) == payload


@pytest.mark.parametrize("fault", ["cancel", "lease", "generation", "original"])
def test_preparation_never_authorizes_changed_current_execution(
    final: Final, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    prepare = commit_module.prepare_originals
    before: dict[str, Any] = {}

    def changed(database: Database, operation: Any) -> Any:
        prepared = prepare(database, operation)
        with database.session() as session:
            run = session.get(RunRecord, final.source.request.run_id)
            assert run is not None
            if fault == "cancel":
                run.status = "cancelled"
            elif fault == "lease":
                run.lease_expires_at = utcnow() - timedelta(seconds=1)
            elif fault == "generation":
                link = session.get(DshSessionLinkRecord, run.run_id)
                assert link is not None
                link.generation += 1
            else:
                evidence = session.get(ResearchEvidenceRecord, SPOT_A)
                assert evidence is not None
                evidence.source_id = "changed after full authentication"
        before.update(_sql(final.source))
        return prepared

    monkeypatch.setattr(commit_module, "prepare_originals", changed)
    with pytest.raises((ValueError, PermissionError)):
        _publish(final)
    assert _sql(final.source) == before


@pytest.mark.parametrize("field,value", [("quality", "candidate"), ("source_id", "changed")])
def test_late_flush_original_change_rolls_back_all_publication_rows(
    final: Final, monkeypatch: pytest.MonkeyPatch, field: str, value: str,
) -> None:
    check = preparation.PreparedOriginals.verify_after_writes
    before = _sql(final.source)

    def changed(self: Any, session: Session) -> None:
        run = session.get(RunRecord, final.source.request.run_id, populate_existing=True)
        assert run is not None and run.artifact_id is not None and run.status == "running"
        session.execute(update(ResearchEvidenceRecord).where(
            ResearchEvidenceRecord.evidence_id == SPOT_A,
        ).values({field: value}).execution_options(synchronize_session=False))
        check(self, session)

    monkeypatch.setattr(preparation.PreparedOriginals, "verify_after_writes", changed)
    with pytest.raises(ValueError, match="original_changed"):
        _publish(final)
    assert _sql(final.source) == before
    with final.source.database.session() as session:
        evidence = session.get(ResearchEvidenceRecord, SPOT_A)
        assert evidence is not None and evidence.quality == "accepted"
        assert evidence.source_id != "changed"


def test_caller_dto_mutation_cannot_change_the_prepared_publication(
    final: Final, monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared = final.reviewed.model_copy(deep=True)
    prepare = commit_module.prepare_originals

    def mutate_caller(database: Database, operation: Any) -> Any:
        prepared = prepare(database, operation)
        assert declared.causal_case is not None
        declared.causal_case.evidence_refs[:] = ["caller-mutated-after-authentication"]
        return prepared

    monkeypatch.setattr(commit_module, "prepare_originals", mutate_caller)
    _publish(final, declared)
    with final.source.database.session() as session:
        row = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.role == "research_publication_source",
        ))
        assert row is not None and "caller-mutated-after-authentication" not in row.payload_json
        outlook = session.scalars(select(AnalyticalOutlookRecord)).first()
        assert outlook is not None
        assessed = assess_outlook_in_session(session, outlook.outlook_id, assessed_at=utcnow())
        assert assessed.source_status == assessed.method_status == "verified"


def test_existing_review_checks_lease_after_the_readset_replay(
    final: Final, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = preparation.PreparedOriginals.verify
    before = _sql(final.source)

    def expires(self: Any, session: Session) -> None:
        verify(self, session)
        session.execute(update(RunRecord).where(
            RunRecord.run_id == final.source.request.run_id,
        ).values(lease_expires_at=utcnow() - timedelta(seconds=1)))

    monkeypatch.setattr(preparation.PreparedOriginals, "verify", expires)
    with pytest.raises(PermissionError, match="permission_lost"):
        ResearchReviewService(final.source.database).review(
            final.source.request.run_id, final.result,
            graph_final_ref=final.reference, expected_lease_owner=OWNER,
        )
    assert _sql(final.source) == before


@pytest.mark.parametrize("existing", [False, True])
def test_native_model_return_checks_fresh_lease_and_rolls_back(
    inputs: PreparedInputs, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    inputs.prepare()
    payload = _payload(inputs)
    claim = RunService(inputs.database).claim_next(
        strategy_version="research.v1", worker_id="model-writer", lease_seconds=300,
    )
    assert claim is not None and claim.run_id == payload.run_id
    service = model_dispatch.ModelDispatchService(inputs.database)
    if existing:
        service.bind(payload)
    before = _sql(inputs)
    verify = preparation.PreparedOriginals.verify

    def expires(self: Any, session: Session) -> None:
        verify(self, session)
        session.execute(update(RunRecord).where(RunRecord.run_id == payload.run_id).values(
            lease_expires_at=utcnow() - timedelta(seconds=1),
        ))

    monkeypatch.setattr(preparation.PreparedOriginals, "verify", expires)
    with pytest.raises(PermissionError, match="permission_lost"):
        service.bind(payload)
    assert _sql(inputs) == before
