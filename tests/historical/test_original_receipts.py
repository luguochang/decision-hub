import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from packages.contracts_py.decision_hub_contracts import DataJobCreate, ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord
from packages.provider_adapters.historical import (
    HistoricalDataArchive,
    HistoricalDataJobRunner,
    HistoricalDataResearchAdapter,
)
from packages.provider_adapters.historical.gate_daily import GateDailyDataLoader
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    RawBodyArchive,
    capture_scope,
)


async def prepared(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'history.sqlite3'}")
    db.create_all()
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=90)
    payload = [[str(int((start + timedelta(days=i)).timestamp())), '2000', str(101+i),
                str(102+i), str(99+i), str(100+i), '20', 'true'] for i in range(90)]
    original = json.dumps(payload).encode()

    async def fetch(url):
        async with CapturingAsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=original, request=req),
        )) as client:
            return (await client.get(url)).json()

    jobs = DataJobService(db)
    job, _ = jobs.create(DataJobCreate(
        idempotency_key='captured-daily', requested_by='owner',
        dataset_ids=['crypto.btc_usdt.ohlcv_1d'], window_start_at=start, window_end_at=end,
    ))
    archive = HistoricalDataArchive(tmp_path / 'historical')
    result = await HistoricalDataJobRunner(jobs, archive, {
        'crypto.btc_usdt.ohlcv_1d': GateDailyDataLoader(fetcher=fetch),
    }).run(job.job_id)
    assert result.status == 'completed'
    manifest = archive.load_coverage(result.coverage_manifest_ref)
    ids = manifest.datasets[0].raw_capture_ids
    assert len(ids) == 1
    with db.session() as session:
        capture = session.get(RawCaptureRecord, ids[0])
        assert RawBodyArchive(tmp_path / 'raw-captures').read(capture.body_ref) == original
    query = ResearchCapabilityQuery(
        schema_version='research-capability-query.v1', request_id='read-history',
        capability_id='data.historical', requirement_id='market_daily_strategy',
        query='daily history', target_url=None, symbols=['crypto.btc_usdt.ohlcv_1d'],
        fields=['row_count'], allowed_domains=[], max_results=1, max_cost_usd=0,
        research_session_id='history-session', round=1, mode='live',
        observed_at=datetime.now(UTC), cutoff_at=datetime.now(UTC),
        window_start_at=start, window_end_at=end,
    )
    batch = CaptureBatch('research:new:read-history', 'unit',
                         RawBodyArchive(tmp_path / 'raw-captures'),
                         lambda _: pytest.fail('must not reinsert historical receipt'))
    return db, jobs, archive, result, manifest, query, batch


async def test_history_reuses_original_receipts_without_refetch_or_timestamp_rewrite(tmp_path):
    db, jobs, archive, _, manifest, query, batch = await prepared(tmp_path)
    with capture_scope(batch):
        result = await HistoricalDataResearchAdapter(jobs, archive).execute(query)
    assert result.facts[0].value == '90'
    assert [c['capture_id'] for c in batch.captures] == manifest.datasets[0].raw_capture_ids
    assert batch.captures[0]['context_id'].startswith('historical:')
    with db.session() as session:
        assert session.query(RawCaptureRecord).count() == 1


@pytest.mark.parametrize('fault', ['missing', 'late', 'corrupt', 'wrong_job', 'legacy'])
async def test_unverifiable_history_never_becomes_research_input(tmp_path, fault):
    db, jobs, archive, result, manifest, query, batch = await prepared(tmp_path)
    with db.session() as session:
        capture = session.get(RawCaptureRecord, manifest.datasets[0].raw_capture_ids[0])
        if fault == 'missing':
            session.delete(capture)
        elif fault == 'late':
            capture.recorded_at = query.cutoff_at + timedelta(seconds=1)
        elif fault == 'corrupt':
            path = tmp_path / 'raw-captures' / capture.body_hash[:2] / capture.body_hash
            path.write_bytes(b'bad')
        elif fault == 'wrong_job':
            capture.context_id = 'historical:unrelated:1:crypto.btc_usdt.ohlcv_1d'
    adapter = HistoricalDataResearchAdapter(jobs, archive)
    if fault == 'legacy':
        manifest.datasets[0].raw_capture_ids = None
        # Old content remains readable, but cannot supply verified new research.
        legacy_ref = archive.write_coverage(manifest)
        adapter._locate = lambda *a, **kw: (legacy_ref, manifest.datasets[0])
    with capture_scope(batch), pytest.raises(ResearchCapabilityError) as rejected:
        await adapter.execute(query)
    assert rejected.value.error_code == 'historical_original_archive_unavailable'
    assert batch.captures == []
