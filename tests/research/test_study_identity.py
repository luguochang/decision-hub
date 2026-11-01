import hashlib
import json
from datetime import UTC, datetime, timedelta

from packages.contracts_py.decision_hub_contracts import (
    MacroEventPayload,
    ObservationCreate,
    TextEnvelope,
)
from packages.contracts_py.decision_hub_contracts.models import GateStatus, SourceManifest
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.application.study import backfill_studies, ensure_study
from packages.kernel.decision_hub_kernel.decision.gate import GateResult
from packages.kernel.decision_hub_kernel.persistence.db import (
    AdmissionDecisionRecord,
    ArtifactRecord,
    Database,
    EventOccurrenceRecord,
    EventWatchRecord,
    MacroEventRecord,
    MacroEventVintageRecord,
    ReportRevisionRecord,
    RunRecord,
    StudyRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollResult
from packages.provider_adapters.research.discovery import CryptoMacroDiscoveryPolicy
from packages.source_adapters.registry import SourceRegistry

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def setup(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'study.sqlite3'}")
    db.create_all()
    return db


def test_report_versions_are_immutable_and_market_scope_separates_same_question(tmp_path):
    db = setup(tmp_path)
    admission = AdmissionService(db, clock=lambda: NOW)
    event_id, _, _ = admission.admit(
        ObservationCreate(text="Study the Fed decision", source_id="manual")
    )
    with db.session() as session:
        btc = ensure_study(
            session,
            question_key="fomc",
            title="FOMC → BTC",
            market="crypto",
            instrument="BTC-USDT-SWAP",
            now=NOW,
        )
        gold = ensure_study(
            session,
            question_key="fomc",
            title="FOMC → 黄金",
            market="gold",
            instrument="XAU-USD",
            now=NOW,
        )
        assert btc != gold
    runs = RunService(db, clock=lambda: NOW)
    first, _ = runs.create(event_id, "first", study_id=btc)
    second, _ = runs.create(event_id, "second", study_id=btc)
    CommitDecisionService(db, clock=lambda: NOW).commit(
        first, event_id, {"summary": "数据不足，等待声明"}, GateResult(GateStatus.research_only, [])
    )
    CommitDecisionService(db, clock=lambda: NOW + timedelta(minutes=5)).commit(
        second,
        event_id,
        {"summary": "声明已到，仍不满足交易门"},
        GateResult(GateStatus.research_only, []),
    )
    with db.session() as session:
        versions = (
            session.query(ReportRevisionRecord).order_by(ReportRevisionRecord.created_at).all()
        )
        assert len(versions) == 2 and versions[1].previous_revision_id == versions[0].revision_id
        assert json.loads(versions[1].change_json)["summary_changed"]
        assert session.get(ArtifactRecord, versions[0].artifact_id).summary == "数据不足，等待声明"
        original = [(v.revision_id, v.change_json, v.recorded_at) for v in versions]
    counts = backfill_studies(db)
    assert backfill_studies(db) == counts
    with db.session() as session:
        versions = (
            session.query(ReportRevisionRecord).order_by(ReportRevisionRecord.created_at).all()
        )
        assert [(v.revision_id, v.change_json, v.recorded_at) for v in versions] == original


class Source:
    manifest = SourceManifest(source_id="fed-press", source_type="official_feed", version="test")

    def __init__(self, envelopes):
        self.envelopes = envelopes

    async def poll(self, cursor=None):
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="next",
            fetched_at=NOW,
            envelopes=tuple(self.envelopes),
        )


def envelope(text, *, payload=None, url=None):
    return TextEnvelope(
        source_id="fed-press",
        source_type="official_feed",
        observed_at=NOW,
        received_at=NOW,
        published_at=NOW,
        raw_text=text,
        language="en",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        macro_event=payload,
        scheduled_at=payload.scheduled_at if payload else None,
        event_family="fomc",
        source_url=url,
    )


async def test_calendar_same_uid_text_revision_keeps_one_watch_and_schedule_change_keeps_history(
    tmp_path,
):
    db = setup(tmp_path)
    payload = MacroEventPayload(
        external_event_id="stable-meeting-uid",
        family="fomc",
        scheduled_at=NOW + timedelta(days=1),
        time_precision="exact",
    )
    source = Source([envelope("FOMC meeting", payload=payload)])
    registry = SourceRegistry()
    registry.register(source)
    ingestion = SourceIngestionService(db, registry, clock=lambda: NOW)
    assert (await ingestion.poll_once("fed-press")).error_code is None
    source.envelopes = [envelope("FOMC meeting, text corrected", payload=payload)]
    assert (await ingestion.poll_once("fed-press")).error_code is None
    with db.session() as session:
        assert session.query(EventWatchRecord).count() == 1
        assert session.query(RunRecord).count() == 1
        assert len({r.occurrence_id for r in session.query(EventOccurrenceRecord)}) == 1

    source.envelopes = [
        envelope(
            "FOMC actual published",
            payload=payload.model_copy(update={"status": "released", "actual_value": 4.0}),
        )
    ]
    assert (await ingestion.poll_once("fed-press")).error_code is None
    with db.session() as session:
        assert session.query(EventWatchRecord).count() == 1
        assert session.query(RunRecord).count() == 2
        actual_run = session.query(RunRecord).filter(RunRecord.available_at.is_(None)).one()
        assert actual_run.event_id != session.query(EventWatchRecord).one().event_id
        actual_event_id = actual_run.event_id
    assert ingestion.event_watches.get_watch_by_event_id(actual_event_id) is not None
    source.envelopes = [
        envelope(
            "FOMC meeting rescheduled",
            payload=payload.model_copy(update={"scheduled_at": NOW + timedelta(days=2)}),
        )
    ]
    assert (await ingestion.poll_once("fed-press")).error_code is None
    with db.session() as session:
        assert session.query(MacroEventRecord).count() == 1
        assert session.query(MacroEventVintageRecord).count() == 4
        watches = session.query(EventWatchRecord).all()
        assert len(watches) == 2 and sum(w.status == "cancelled" for w in watches) == 1
        assert session.query(RunRecord).filter_by(status="cancelled").count() == 1
        assert len({r.occurrence_id for r in session.query(EventOccurrenceRecord)}) == 1


async def test_discovery_reasons_budget_and_duplicate_admission_are_durable(tmp_path):
    db = setup(tmp_path)
    source = Source(
        [
            envelope("FOMC statement", url="https://example.test/a"),
            envelope("Inflation statement", url="https://example.test/b"),
            envelope("Routine notice", url="https://example.test/c"),
        ]
    )
    registry = SourceRegistry()
    registry.register(source)
    policy = CryptoMacroDiscoveryPolicy(
        source_ids=["fed-press"], high_impact_terms=["fomc", "inflation"], max_daily_research=1
    )
    ingestion = SourceIngestionService(db, registry, clock=lambda: NOW, strategy_selector=policy)
    assert (await ingestion.poll_once("fed-press")).error_code is None
    assert (await ingestion.poll_once("fed-press")).error_code is None
    with db.session() as session:
        assert session.query(RunRecord).filter_by(strategy_version="research.v1").count() == 1
        reasons = {r.reason_code for r in session.query(AdmissionDecisionRecord)}
        assert {
            "high_impact_title",
            "same_input_already_admitted",
            "research_admission_budget_exhausted",
            "no_high_impact_title_match",
        } <= reasons
        assert session.query(StudyRecord).count() == 3


def test_legacy_unknown_scope_does_not_silently_enable_rechecks(tmp_path):
    from packages.kernel.decision_hub_kernel.application.review_schedule import (
        ReviewScheduleService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import EventRecord, ResearchResultRecord
    from tests.research.test_review_schedule import NOW as review_now
    from tests.research.test_review_schedule import _result

    db = setup(tmp_path)
    result = _result()
    with db.session() as session:
        session.add(
            EventRecord(
                event_id="legacy",
                event_type="macro_event",
                occurred_at=review_now,
                received_at=review_now,
            )
        )
        session.add(
            RunRecord(
                run_id="legacy-run",
                event_id="legacy",
                strategy_version="research.v1",
                status="completed",
                created_at=review_now,
                updated_at=review_now,
                finished_at=review_now,
            )
        )
        session.add(
            ResearchResultRecord(
                run_id="legacy-run",
                schema_version=result.schema_version,
                payload_json=result.model_dump_json(),
                payload_hash="f" * 64,
                created_at=review_now,
            )
        )
    backfill_studies(db)
    assert ReviewScheduleService(db, clock=lambda: review_now).plan_due() == ()
    with db.session() as session:
        study = session.query(StudyRecord).one()
        assert study.identity_basis == "legacy_unknown" and study.status == "paused"
        assert session.query(RunRecord).count() == 1
        study.status = "active"
    backfill_studies(db)
    # Flipping tracking status alone does not qualify an unknown historical market.
    assert ReviewScheduleService(db, clock=lambda: review_now).plan_due() == ()
    with db.session() as session:
        assert session.query(RunRecord).count() == 1
