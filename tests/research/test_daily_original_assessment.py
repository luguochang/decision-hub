"""UNWIRED TEST DRAFT: actual archived Gate daily input and real publication.

Requires the stage normalizer/reader to be applied and Root's independent daily
assessor mapping first. The historical producer is compiled from Root's exact
preserved old source. No source, admission, Graph, reviewer or publication pass
is mocked, no existing execution proof is deleted, and no formal DB is opened.
The two signed false-receipt controls intentionally alter the real adapter's
typed return before genuine Gateway admission; they do not inject a verdict.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import sys
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import httpx
import pytest
import yaml
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DataJobCreate,
    DshSessionResult,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    FactEnvelope,
    ObservationCreate,
    ResearchCapabilityInvocation,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.evals.analytical_assessment import assess_outlook_in_session
from packages.evals.daily_assessment import assess_daily_source_in_session
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.fact_lineage import run_scope
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    Database,
    FactVersionRecord,
    RawCaptureRecord,
    ResearchFactRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.provider_adapters.historical import HistoricalDataArchive, HistoricalDataJobRunner
from packages.provider_adapters.historical import research as current_producer_module
from packages.provider_adapters.historical.gate_daily import GateDailyDataLoader
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient, RawBodyArchive
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import OWNER
from tests.research.test_durable_research_gateway import (
    _manifest,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import (  # pyright: ignore[reportPrivateUsage]
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)
from tests.research.test_graph_citation_membership_v2 import SPOT_A, SnapshotCitationHost

ROOT = Path(__file__).resolve().parents[2]  # When placed at tests/research, not stage.
DATASET = "crypto.btc_usdt.ohlcv_1d"
FIELDS = [
    "period_close",
    "sma_20",
    "sma_60",
    "return_20_pct",
    "realized_vol_20_pct",
    "volume_ratio_20",
    "rolling_high_20",
    "rolling_low_20",
    "row_count",
]
pytestmark = pytest.mark.usefixtures("forbid_external_connections")


class _NetworkFixture(Protocol):
    __wrapped__: Callable[[pytest.MonkeyPatch], None]


@dataclass(frozen=True)
class PublishedDaily:
    inputs: PreparedInputs
    directory: Path
    outlook_id: str
    capture_id: str
    original_body: bytes
    result: ResearchCapabilityResult | None = None


@contextmanager
def _legacy_producer() -> Generator[ModuleType]:
    """Use the real preserved creator, not today's producer with proof removed."""
    base = (
        ROOT / "data/product-local/remediation/W7/graph-citation-v2-20261004/daily-source-before01"
    )
    seal = json.loads((base / "source-seal.json").read_text(encoding="utf-8-sig"))
    records = {row["path"].replace("\\", "/"): row for row in seal["records"]}
    relative = "packages/provider_adapters/historical/research.py"
    original = base / relative
    recorded = records[relative]
    assert recorded["existed"] is True and original.is_file()
    payload = original.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == recorded["sha256"].lower()
    # The old creator imports this existing pure algorithm. Do not quietly
    # combine it with a future different algorithm and call it an old fixture.
    strategy = "packages/kernel/decision_hub_kernel/decision/daily_strategy.py"
    assert (
        hashlib.sha256((ROOT / strategy).read_bytes()).hexdigest()
        == records[strategy]["sha256"].lower()
    )
    name = "packages.provider_adapters.historical._preserved_daily_test_creator"
    assert name not in sys.modules
    module = ModuleType(name)
    module.__file__ = str(original)
    module.__package__ = "packages.provider_adapters.historical"
    sys.modules[name] = module
    try:
        exec(compile(payload, str(original), "exec"), module.__dict__)
        assert "daily_execution_receipt_json" not in payload.decode()
        yield module
    finally:
        assert sys.modules.pop(name) is module


@contextmanager
def _producer(current_producer: bool) -> Generator[ModuleType]:
    if current_producer:
        yield current_producer_module
    else:
        with _legacy_producer() as original:
            yield original


def _resign_false_receipt(
    result: ResearchCapabilityResult, damage: str | None
) -> ResearchCapabilityResult:
    if damage is None:
        return result
    facts = []
    for fact in result.facts or []:
        attributes = dict(fact.attributes)
        receipt = json.loads(str(attributes["daily_strategy_receipt_json"]))
        if damage == "features":
            receipt["features"]["close"] += 1.0
        else:
            assert damage == "rationale"
            receipt["rationale"] += " Controlled false source explanation."
        attributes["daily_strategy_receipt_json"] = json.dumps(
            receipt,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        altered = fact.model_copy(update={"attributes": attributes})
        keys = (
            "requirement_id",
            "metric_family",
            "field",
            "instrument",
            "venue",
            "value",
            "unit",
            "window_start_at",
            "window_end_at",
            "event_offset",
            "published_at",
            "source_id",
            "independence_group",
            "delay_class",
            "payload_schema_ref",
            "attributes",
        )
        digest = research_fact_payload_hash(**{key: getattr(altered, key) for key in keys})
        facts.append(
            altered.model_copy(
                update={
                    "payload_hash": digest,
                    "fact_id": research_fact_instance_id(
                        evidence_id=altered.evidence_id, payload_hash=digest
                    ),
                }
            )
        )
    return result.model_copy(update={"facts": facts})


async def _create_published_daily(
    directory: Path,
    *,
    damage: str | None = None,
    current_producer: bool = False,
    publish: bool = True,
    after_actual: Callable[
        [PreparedInputs, ResearchCapabilityResult], ResearchCapabilityResult | None
    ]
    | None = None,
) -> PublishedDaily:
    directory.mkdir(parents=True, exist_ok=True)
    database = Database(f"sqlite+pysqlite:///{(directory / 'daily.sqlite3').as_posix()}")
    database.create_all()
    history = HistoricalDataArchive(directory / "historical")
    end = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=90)
    raw = [
        [
            str(int((start + timedelta(days=index)).timestamp())),
            "1200000",
            str(60001 + index),
            str(60002 + index),
            str(59999 + index),
            str(60000 + index),
            "20",
            "true",
        ]
        for index in range(90)
    ]
    body = json.dumps(raw).encode()
    actual_urls: list[str] = []

    async def fetch(url: str) -> object:
        actual_urls.append(url)
        # CapturingAsyncClient still performs the real response/body receipt
        # handling. Only the external exchange transport is controlled offline.
        async with CapturingAsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=body, request=request, headers={"content-type": "application/json"}
                ),
            )
        ) as client:
            return (await client.get(url)).json()

    jobs = DataJobService(database)
    job, created = jobs.create(
        DataJobCreate(
            idempotency_key="genuine-gate-daily",
            requested_by="owner",
            dataset_ids=[DATASET],
            window_start_at=start,
            window_end_at=end,
        )
    )
    assert created
    completed = await HistoricalDataJobRunner(
        jobs,
        history,
        {
            DATASET: GateDailyDataLoader(fetcher=fetch),
        },
    ).run(job.job_id)
    assert completed.status == "completed" and len(actual_urls) == 1
    assert completed.coverage_manifest_ref is not None
    coverage = history.load_coverage(completed.coverage_manifest_ref)
    capture_ids = coverage.datasets[0].raw_capture_ids
    assert capture_ids is not None and len(capture_ids) == 1
    with database.session() as session:
        capture = session.get(RawCaptureRecord, capture_ids[0])
        assert capture is not None and capture.body_ref is not None
        assert RawBodyArchive(directory / "raw-captures").read(capture.body_ref) == body
    now = utcnow()
    event_id, _, admitted = AdmissionService(database).admit(
        ObservationCreate(
            text="Current BTC spot price and volume with completed UTC daily trend context.",
            source_id="daily-original-controlled-exchange",
            source_type=SourceType.transcript,
            observed_at=now - timedelta(seconds=1),
            published_at=now - timedelta(seconds=2),
            language="en",
            event_hint="crypto_market_snapshot",
        )
    )
    assert admitted
    run_id, created = RunService(database).create(event_id, strategy_version="research.v1")
    assert created
    pack_root, manifest = _prepared_pack(directory)
    manifest["execution_budget"]["max_evidence_rounds"] = 1
    manifest["execution_budget"]["max_structured_repairs"] = 0
    manifest["execution_budget"]["total_deadline_seconds"] = 900
    (pack_root / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    request = ResearchRequestFactory(
        database,
        pack_root=pack_root,
        execution_mode="live" if current_producer else "replay",
        allowed_capabilities=(
            "replay.research",
            "data.historical",
            "document.readback",
            "web.search",
        ),
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
    ).build(event_id, run_id)
    assert "data.historical" in request.allowed_capabilities
    assert RunService(database).claim_for_execution(
        run_id,
        worker_id=OWNER,
        lease_seconds=900,
        strategy_version="research.v1",
    )
    sid, rid = DshSessionLinkService.deterministic_ids(run_id, research_request_hash(request))
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run_id,
        request_hash=research_request_hash(request),
        deterministic_session_id=sid,
        deterministic_request_id=rid,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=request.deadline_at,
        max_tool_calls=request.execution_budget.max_tool_calls,
        model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
        generation=1,
    )
    inputs = PreparedInputs(
        database,
        request,
        submit,
        DshUpstreamIdentity(
            source_commit="1" * 40,
            source_version="0.1.0",
            package_versions={},
            plugin_build_hash="2" * 64,
        ),
        "Actual Factory prompt is prepared by Runtime",
        utcnow(),
    )
    archive = RawBodyArchive(directory / "raw-captures")
    with _producer(current_producer) as implementation:
        producer_type = implementation.HistoricalDataResearchAdapter

        class OriginalAdapter(producer_type):
            async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
                actual = await super().execute(query)
                if current_producer:
                    assert all(
                        "daily_execution_receipt_json" in item.attributes
                        for item in actual.facts or []
                    )
                else:
                    assert all(
                        "daily_execution_receipt_json" not in item.attributes
                        for item in actual.facts or []
                    )
                if after_actual is not None:
                    changed = after_actual(inputs, actual)
                    if changed is not None:
                        actual = changed
                return _resign_false_receipt(actual, damage)

        adapter = (
            OriginalAdapter(jobs, history)
            if current_producer
            else OriginalAdapter(jobs, history, clock=lambda: request.pit_cutoff_at)
        )
        capability_manifest = _manifest().model_copy(
            update={
                "capability_id": "data.historical",
                "allowed_domains": ["api.gateio.ws"],
            }
        )
        gateway = DurableResearchCapabilityGateway(
            ResearchCapabilityGatewayService(
                [capability_manifest], [adapter], enabled_capabilities=["data.historical"]
            ),
            DshSessionLinkService(database),
            ResearchEvidenceService(database),
            ResearchObservabilityService(database),
            requirements={item.requirement_id: item for item in request.evidence_requirements},
            raw_archive=archive,
            parser_fingerprint="controlled-original-daily-source.v1",
        )

        actual_daily: ResearchCapabilityResult | None = None

        async def actual_call(
            payload: DshSessionSubmit,
        ) -> tuple[ResearchCapabilityQuery, ResearchCapabilityResult]:
            query = ResearchCapabilityQuery(
                schema_version="research-capability-query.v1",
                request_id="actual-original-gate-daily",
                capability_id="data.historical",
                requirement_id="market_regime_context",
                query="Read the original 90 completed UTC Gate spot days.",
                target_url=None,
                symbols=[DATASET],
                fields=FIELDS,
                allowed_domains=[],
                max_results=1,
                max_cost_usd=0,
                research_session_id=payload.deterministic_session_id,
                round=payload.generation,
                mode="live" if current_producer else "replay",
                observed_at=request.pit_cutoff_at,
                cutoff_at=request.deadline_at if current_producer else request.pit_cutoff_at,
                window_start_at=start,
                window_end_at=end,
            )
            if current_producer:
                intent = ResearchCapabilityInvocation.model_validate(
                    {
                        **query.model_dump(
                            exclude={
                                "schema_version",
                                "round",
                                "mode",
                                "observed_at",
                                "requested_observed_at",
                                "cutoff_at",
                                "deadline_at",
                            }
                        ),
                        "schema_version": "research-capability-invocation.v1",
                    }
                )
                actual = await gateway.invoke_live(intent)
            else:
                actual = await gateway.execute(query)
            return query, actual

        if not publish:
            # Actual current Factory/Execution/Prompt/Link and real admission,
            # with no invented Graph publication for a cancelled/rejected Run.
            with database.session() as session:
                bundle = read_research_bundle_in_session(session, run_id)
            assert isinstance(bundle, FrozenResearchBundle)
            prepared_inputs = replace(
                inputs,
                prompt=render_prepared_research_policy(
                    bundle,
                    request.allowed_capabilities,
                )
                + "\n\nControlled actual daily Gateway invocation; no Provider.",
            )
            prepared_inputs.prepare()
            _, actual_daily = await actual_call(submit)
            return PublishedDaily(inputs, directory, "", capture_ids[0], body, actual_daily)

        class RealDailyHost(SnapshotCitationHost):
            async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
                nonlocal actual_daily
                started_at = utcnow()
                query, actual = await actual_call(payload)
                actual_daily = actual
                assert actual.facts and len(actual.facts) == len(FIELDS)
                assert all(item.quality == "accepted" for item in actual.facts)
                assert all(item.quality == "accepted" for item in actual.evidence_candidates)
                response = await super().result(payload)
                events = json.loads(response.events_json)
                if current_producer:
                    # First construction of this controlled MCP original, before
                    # Runtime records it. Live Factory PIT is a deadline ceiling,
                    # so the replay parent's PIT clock cannot timestamp its sources.
                    received_at = utcnow()
                    snapshot = events[1]["payload"]["event"]["data"]["message"]["content"][0][
                        "structuredContent"
                    ]
                    candidates = []
                    for saved in snapshot["evidence_candidates"]:
                        original = EvidenceCandidate.model_validate(saved)
                        values = original.model_dump(mode="json")
                        values.update(
                            published_at=None, observed_at=started_at, received_at=received_at
                        )
                        values["content_hash"] = research_evidence_content_hash(
                            **{
                                key: values[key]
                                for key in (
                                    "requirement_id",
                                    "kind",
                                    "authority",
                                    "source_id",
                                    "source_url",
                                    "published_at",
                                    "excerpt",
                                    "structured_payload_ref",
                                )
                            }
                        )
                        candidates.append(EvidenceCandidate.model_validate(values))
                    source_times = {item.evidence_id: item for item in candidates}
                    facts = []
                    for saved in snapshot["facts"]:
                        original = FactEnvelope.model_validate(saved)
                        values = original.model_dump()
                        evidence = source_times[original.evidence_id]
                        values.update(
                            published_at=evidence.published_at,
                            observed_at=evidence.observed_at,
                            received_at=evidence.received_at,
                        )
                        digest = research_fact_payload_hash(
                            **{
                                key: values[key]
                                for key in (
                                    "requirement_id",
                                    "metric_family",
                                    "field",
                                    "instrument",
                                    "venue",
                                    "value",
                                    "unit",
                                    "window_start_at",
                                    "window_end_at",
                                    "event_offset",
                                    "published_at",
                                    "source_id",
                                    "independence_group",
                                    "delay_class",
                                    "payload_schema_ref",
                                    "attributes",
                                )
                            }
                        )
                        values.update(
                            payload_hash=digest,
                            fact_id=research_fact_instance_id(
                                evidence_id=original.evidence_id,
                                payload_hash=digest,
                            ),
                        )
                        facts.append(FactEnvelope.model_validate(values))
                    snapshot["evidence_candidates"] = [
                        item.model_dump(mode="json") for item in candidates
                    ]
                    snapshot["facts"] = [item.model_dump(mode="json") for item in facts]
                    snapshot["completed_at"] = received_at.isoformat()
                call, reply = copy.deepcopy(events[:2])
                call["payload"]["event"]["data"]["message"]["content"][0]["toolCallId"] = (
                    query.request_id
                )
                content = reply["payload"]["event"]["data"]["message"]["content"][0]
                content["toolCallId"], content["structuredContent"] = (
                    query.request_id,
                    actual.model_dump(mode="json"),
                )
                events.extend((call, reply))
                for index, entry in enumerate(events, start=1):
                    entry["payload"]["event"]["seq"] = index
                encoded = json.dumps(events)
                return response.model_copy(
                    update={
                        "events_json": encoded,
                        "last_seq": len(events),
                        **(
                            {"started_at": started_at, "finished_at": utcnow()}
                            if current_producer
                            else {}
                        ),
                        "result_hash": hashlib.sha256(
                            (response.final_response + "\0" + encoded).encode()
                        ).hexdigest(),
                    }
                )

        runtime = DshWebResearchRuntime(
            DshSessionLinkService(database),
            RealDailyHost(inputs, top_ref=SPOT_A),
            poll_interval_seconds=0.001,
        )
        try:
            graph = build_agentic_research_graph(
                runtime, ResearchEvidenceService(database), expected_lease_owner=OWNER
            )
            state = await graph.ainvoke(initial_research_state(request))
            reference = binding.parse_graph_final_ref(state["graph_final_projection_ref"])
            with database.session() as session:
                final = binding.read_graph_final_in_session(
                    session,
                    run_id=run_id,
                    reference=reference,
                    expected_lease_owner=OWNER,
                    require_current_permission=True,
                )
            review = ResearchReviewService(database).review(
                run_id,
                final.result,
                graph_final_ref=reference,
                expected_lease_owner=OWNER,
            )
            CommitDecisionService(database).commit_research_result(
                run_id,
                event_id,
                final.result.model_copy(update={"reviews": [review]}),
                graph_final_ref=reference,
                expected_lease_owner=OWNER,
            )
        finally:
            await runtime.close()
    with database.session() as session:
        outlook = session.scalars(
            select(AnalyticalOutlookRecord).where(
                AnalyticalOutlookRecord.run_id == run_id,
                AnalyticalOutlookRecord.method_family == "daily_rule",
            )
        ).one()
        assert outlook.horizon == "7d" and outlook.method_version == "daily-trend-v0.1"
        assert run_scope(session, run_id) == ("crypto", "BTC-USDT-SWAP")
        assert outlook.instrument == "BTC-USDT" and outlook.action == "long"
        method_role = session.scalar(
            select(RunRoleOutputRecord.output_id).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.schema_version == "daily-execution-admission.v1",
            )
        )
        if current_producer:
            assert method_role is not None
        else:
            assert method_role is None
        identity = outlook.outlook_id
    return PublishedDaily(inputs, directory, identity, capture_ids[0], body, actual_daily)


@pytest.fixture(scope="module")
async def original_seed(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[PublishedDaily]:
    network = pytest.MonkeyPatch()
    cast(_NetworkFixture, forbid_external_connections).__wrapped__(network)
    seeded: PublishedDaily | None = None
    try:
        seeded = await _create_published_daily(tmp_path_factory.mktemp("genuine-daily-old-source"))
        yield seeded
    finally:
        if seeded is not None:
            seeded.inputs.database.engine.dispose()
        network.undo()


@pytest.fixture
def original(original_seed: PublishedDaily, tmp_path: Path) -> Iterator[PublishedDaily]:
    source = original_seed.inputs.database.sqlite_path
    assert source is not None
    destination = tmp_path / "isolated.sqlite3"
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as reader:
        with sqlite3.connect(destination) as writer:
            reader.backup(writer)
    shutil.copytree(original_seed.directory / "raw-captures", tmp_path / "raw-captures")
    database = Database(f"sqlite+pysqlite:///{destination.as_posix()}")
    copied = replace(
        original_seed, inputs=replace(original_seed.inputs, database=database), directory=tmp_path
    )
    try:
        yield copied
    finally:
        database.engine.dispose()


def _row_hashes(source: PublishedDaily) -> dict[str, list[str]]:
    path = source.inputs.database.sqlite_path
    assert path is not None
    result = {}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for (name,) in tables:
            assert '"' not in name
            rows = connection.execute(f'SELECT * FROM "{name}"').fetchall()
            result[name] = sorted(
                hashlib.sha256(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        default=lambda value: (
                            value.hex() if isinstance(value, bytes) else str(value)
                        ),
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                for row in rows
            )
    return result


def _assess_read_only(source: PublishedDaily):
    path = source.inputs.database.sqlite_path
    assert path is not None
    engine = create_engine(
        "sqlite+pysqlite://",
        creator=lambda: sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        ),
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    checkouts: list[object] = []
    statements: list[str] = []

    def checked_out(connection: object, _record: object, _proxy: object) -> None:
        checkouts.append(connection)

    def observed(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "checkout", checked_out)
    event.listen(engine, "before_cursor_execute", observed)
    before = _row_hashes(source)
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("BEGIN")
            outlook = session.get(AnalyticalOutlookRecord, source.outlook_id)
            assert outlook is not None
            archive = RawBodyArchive(source.directory / "raw-captures")
            now = utcnow()
            replay = assess_daily_source_in_session(
                session, outlook, assessed_at=now, archive=archive
            )
            public = assess_outlook_in_session(
                session, source.outlook_id, assessed_at=now, archive=archive
            )
            assert not session.new and not session.dirty and not session.deleted
        assert len(checkouts) == 1
        assert statements and all(
            statement.lstrip().split(None, 1)[0].upper()
            in {
                "SELECT",
                "BEGIN",
                "PRAGMA",
                "WITH",
            }
            for statement in statements
        )
        assert _row_hashes(source) == before
        return replay, public
    finally:
        engine.dispose()


def test_genuine_old_gate_source_and_perpetual_study_spot_outlook(original: PublishedDaily) -> None:
    before = _row_hashes(original)
    replay, public = _assess_read_only(original)
    assert replay.source_status == "verified" and replay.reason is None
    assert replay.method_status == "unverifiable" and replay.stable_recipe_hash is None
    assert "original_execution_identity_unattested" in replay.limitations
    assert replay.refs and any(ref.role == "research_publication_source" for ref in replay.refs)
    assert public.source_status == "verified" and public.method_status == "unverifiable"
    assert public.stable_recipe_hash is None and not public.directional_usable
    assert public.label_status == "not_due"
    assert _row_hashes(original) == before
    with original.inputs.database.session() as session:
        capture = session.get(RawCaptureRecord, original.capture_id)
        assert capture is not None and capture.body_ref is not None
        assert (
            RawBodyArchive(original.directory / "raw-captures").read(capture.body_ref)
            == original.original_body
        )


@pytest.mark.parametrize("fault", ["archive", "job", "url", "capture_pit", "spot_target"])
def test_original_boundaries_do_not_become_source_verified(
    original: PublishedDaily, fault: str
) -> None:
    with original.inputs.database.session() as session:
        capture = session.get(RawCaptureRecord, original.capture_id)
        outlook = session.get(AnalyticalOutlookRecord, original.outlook_id)
        assert capture is not None and outlook is not None
        if fault == "archive":
            assert capture.body_hash is not None
            (
                original.directory / "raw-captures" / capture.body_hash[:2] / capture.body_hash
            ).write_bytes(b"wrong body")
        elif fault == "job":
            capture.context_id = f"historical:datajob-no-such-original:1:{DATASET}"
        elif fault == "url":
            capture.url = capture.url.replace("currency_pair=BTC_USDT", "currency_pair=ETH_USDT")
        elif fault == "capture_pit":
            cutoff = as_utc(outlook.window_start_at)
            assert cutoff is not None
            capture.recorded_at = cutoff + timedelta(seconds=1)
        else:
            outlook.instrument = "ETH-USDT"
    replay, public = _assess_read_only(original)
    assert replay.source_status in {"invalid", "unverifiable"} and replay.reason
    assert public.source_status != "verified" and public.label_status == "not_due"
    assert replay.method_status != "verified" and replay.stable_recipe_hash is None
    assert not public.directional_usable


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["features", "rationale"])
async def test_true_gateway_acceptance_and_publication_do_not_certify_false_resigned_receipt(
    tmp_path: Path,
    damage: str,
) -> None:
    source = await _create_published_daily(tmp_path / damage, damage=damage)
    try:
        # The adversarial original really passed the unchanged typed Gateway
        # and real publication chain; there is no monkeypatched source pass.
        with source.inputs.database.session() as session:
            assert session.query(ResearchFactRecord).filter_by(
                run_id=source.inputs.request.run_id,
                capability_id="data.historical",
                quality="accepted",
            ).count() == len(FIELDS)
            assert session.query(FactVersionRecord).count() >= len(FIELDS)
        replay, public = _assess_read_only(source)
        assert (
            replay.source_status == "invalid"
            and replay.reason == "daily_full_strategy_replay_mismatch"
        )
        assert public.source_status == "invalid" and public.label_status == "not_due"
        assert replay.stable_recipe_hash is None and not public.directional_usable
    finally:
        source.inputs.database.engine.dispose()
