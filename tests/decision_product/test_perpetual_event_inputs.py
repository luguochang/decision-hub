"""CPI source originals reach a real perpetual Task/root/reviewer; no HTTP/Host/model."""

import io
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from packages.contracts_py.decision_hub_contracts import TextEnvelope
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord, SourcePollAttemptRecord
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.source_adapters.official_feeds.diagnostics import response_snapshot
from tests.decision_product.test_okx_perpetual_original import persisted_original as persisted_original
from tests.decision_product.test_perpetual_deadline_separation import _paper
from tests.decision_product.test_perpetual_research_binding import AT, OWNER, binding_case as binding_case


def _period_pdf():
    text = "The Consumer Price Index news release for October 2026 is scheduled to be published on Tuesday, November 10, 2026, at 8:30 a.m. (ET)."
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    escaped = text.replace('(', r'\(').replace(')', r'\)')
    stream.set_data(('BT /F1 12 Tf 20 20 Td (' + escaped + ') Tj ET').encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _sources(case):
    """Archived synthetic official-format bytes through unchanged native readers."""
    from packages.kernel.decision_hub_kernel.ports.bls_cpi import BLS_SERIES, BLS_SOURCE_ID, BLS_URL, BLS_VERSION, parse_bls_cpi_metrics
    from packages.kernel.decision_hub_kernel.ports.cpi_nowcast import NOWCAST_SOURCE_ID, NOWCAST_URL, NOWCAST_VERSION, parse_cpi_nowcast_metrics
    from packages.kernel.decision_hub_kernel.ports.cpi_schedule import CPI_SCHEDULE_SOURCE_ID, CPI_SCHEDULE_VERSION, DOL_DOCUMENT_URL, fred_calendar_url, parse_cpi_publication_schedules
    from packages.provider_adapters.research.raw_capture import safe_url

    received = AT - timedelta(seconds=4)

    def capture(source_id, suffix, raw, url, version):
        capture_id, attempt = 'cpi-input-' + suffix, 'cpi-input-' + source_id
        digest, ref = case['archive'].put(raw)
        with case['db'].session() as session:
            session.add(RawCaptureRecord(capture_id=capture_id, context_id='source:' + attempt, url=safe_url(url), status_code=200,
                body_hash=digest, body_ref=ref, byte_count=len(raw), media_type='application/octet-stream', adapter_version=version, error_code=None,
                started_at=AT - timedelta(seconds=6), completed_at=received, recorded_at=AT - timedelta(seconds=3)))
        return dict(capture_id=capture_id, raw_body_hash=digest, raw_body_ref=ref, url=url, received_at=received)

    def admit(source_id, version, raw, payloads, url, *, scheduled=False):
        snapshot = response_snapshot(200, raw, version).model_copy(update={'parse_status': 'valid', 'parsed_count': len(payloads)})
        with case['db'].session() as session:
            session.add(SourcePollAttemptRecord(attempt_id='cpi-input-' + source_id, source_id=source_id, trigger_type='scheduled', status='succeeded',
                started_at=AT - timedelta(seconds=8), completed_at=AT - timedelta(seconds=1), received_count=len(payloads), accepted_count=len(payloads),
                duplicate_count=0, revision_count=0, latency_ms=1, error_code=None, content_snapshot_json=snapshot.model_dump_json()))
        envelopes = tuple(TextEnvelope(source_id=source_id, source_type='official_feed', observed_at=received, received_at=received, published_at=None,
            raw_text=json.dumps(value.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, separators=(',', ':')), language='en',
            event_hint=value.event_key if scheduled else 'cpi_nowcast_metric' if source_id == NOWCAST_SOURCE_ID else 'cpi_index_metric',
            scheduled_at=value.scheduled_at if scheduled else None, source_url=url, content_hash=canonical_hash(value.model_dump(mode='json')))
            for value in payloads)
        assert all(result[2] for result in AdmissionService(case['db'], clock=lambda: AT - timedelta(seconds=2)).admit_envelopes(envelopes))

    calendar = b'<h1>2026 Economic Release Calendar - Consumer Price Index</h1><p>All times are US Central Time.</p><table><tr><td>Tuesday November 10, 2026</td></tr><tr><td>7:30 AM</td><td>Consumer Price Index</td></tr></table>'
    period = _period_pdf()
    fred = capture(CPI_SCHEDULE_SOURCE_ID, 'fred', calendar, fred_calendar_url(2026), CPI_SCHEDULE_VERSION)
    dol = capture(CPI_SCHEDULE_SOURCE_ID, 'dol', period, DOL_DOCUMENT_URL, CPI_SCHEDULE_VERSION)
    schedules = parse_cpi_publication_schedules(calendar, period, year=2026, fred_capture=fred, dol_capture=dol, attempt_id='cpi-input-' + CPI_SCHEDULE_SOURCE_ID)
    admit(CPI_SCHEDULE_SOURCE_ID, CPI_SCHEDULE_VERSION, calendar, schedules, fred_calendar_url(2026), scheduled=True)

    definitions = '<p>If the cell is blank, it implies that the actual data corresponding to the month</p><p>seasonally adjusted, month-over-month inflation rates</p><p>based on nonseasonally adjusted data for CPI inflation and core CPI inflation</p>'
    header = '<tr><th>Month</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>'
    nowcast = (definitions + ''.join('<table><caption>' + caption + '</caption>' + header + '<tr><td>October 2026</td><td>' + values[0] + '</td><td>' + values[1] + '</td><td>0.1</td><td>0.1</td><td>10/17</td></tr></table>'
        for caption, values in [('Inflation, month-over-month percent change', ('0.31', '0.22')), ('Inflation, year-over-year percent change', ('3.1', '2.2'))])).encode()
    nc = capture(NOWCAST_SOURCE_ID, 'nowcast', nowcast, NOWCAST_URL, NOWCAST_VERSION)
    estimates = parse_cpi_nowcast_metrics(nowcast, reference_period='2026-10', provenance={'capture_id': nc['capture_id'], 'attempt_id': 'cpi-input-' + NOWCAST_SOURCE_ID, 'received_at': received})
    admit(NOWCAST_SOURCE_ID, NOWCAST_VERSION, nowcast, estimates, NOWCAST_URL)

    bls = json.dumps({'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [{'seriesID': sid, 'data': [
        {'year': str(year), 'period': f'M{month:02}', 'value': str(100 + (year - 2025) * 12 + month - 1 + offset * 10)}
        for year in (2025, 2026) for month in range(1, 13)]} for offset, (sid, _, _) in enumerate(BLS_SERIES.values())]}}).encode()
    bc = capture(BLS_SOURCE_ID, 'bls', bls, BLS_URL, BLS_VERSION)
    metrics = parse_bls_cpi_metrics(bls, reference_period='2026-09', provenance={'capture_id': bc['capture_id'], 'attempt_id': 'cpi-input-' + BLS_SOURCE_ID,
        'received_at': received, 'request_startyear': 2025, 'request_endyear': 2026})
    admit(BLS_SOURCE_ID, BLS_VERSION, bls, metrics, BLS_URL)


@pytest.mark.parametrize('horizon', ['4h', '24h'])
def test_current_paper_cpi_parts_reach_real_root_prompt_and_review_catalog(binding_case, tmp_path, monkeypatch, horizon):
    from packages.kernel.decision_hub_kernel.application.cpi_pre_event_context import PREFIX, collect_cpi_pre_event_context_in_session
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import CollectedPerpetualCloseWindow
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import PerpetualResearchBindingService, read_perpetual_research_materials_in_session
    from packages.kernel.decision_hub_kernel.application.perpetual_research_entry import PerpetualResearchEntryService
    from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import parse_perpetual_research_command
    from packages.kernel.decision_hub_kernel.application.research_binding import read_research_bundle_in_session
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import read_prepared_execution_in_session
    from tests.decision_product.test_perpetual_research_binding import factory_for, prepare_execution
    from tests.decision_product.test_perpetual_research_entry import command

    case = binding_case
    from packages.provider_adapters.research.raw_capture import RawBodyArchive
    cpi_archive = RawBodyArchive(tmp_path / 'data' / 'raw-captures')
    _paper(case)
    _sources({**case, 'archive': cpi_archive})
    with case['db'].session() as session:
        source = collect_cpi_pre_event_context_in_session(session, cutoff_at=AT, archive=cpi_archive)
    assert source.reference_period == '2026-10' and len(source.nowcast) == len(source.prior) == 4
    assert next(point.value for point in source.nowcast if point.metric_id == 'headline_mom') == Decimal('0.31')
    assert {point.reference_period for point in source.prior} == {'2026-09'}
    assert all(point.source.known_at == AT - timedelta(seconds=1) for point in (*source.nowcast, *source.prior))

    binding = PerpetualResearchBindingService(case['db'], case['source'], now=lambda: AT)
    entry = PerpetualResearchEntryService(case['db'], binding, total_deadline_seconds=3600, collect_window=None, now=lambda: AT)
    requested = parse_perpetual_research_command({**command(case), 'schema_version': 'perpetual-research-command.v3', 'request_id': 'cpi-native-' + horizon,
        'horizon': horizon, 'input_policy': 'saved_preferences_current_paper_ledger.v1'})
    prepared = entry.prepare(OWNER, requested, CollectedPerpetualCloseWindow(case['ids'], AT, AT))
    accepted = entry.commit(OWNER, requested, prepared)
    current = {**case, 'run_id': accepted.run_id, 'event_id': accepted.event_id}
    factory, _ = factory_for(current, tmp_path, execution_mode='live')
    request = factory.build(accepted.event_id, accepted.run_id)
    parts = [item for item in request.input_evidence if item.source_id.startswith(PREFIX)]
    assert len(parts) == 3, 'perpetual Task must freeze all three authenticated CPI parts'
    assert request.context_manifest.rules_ref.endswith('protected.v3')
    bodies = {json.loads(item.excerpt)['part']: json.loads(item.excerpt) for item in parts}
    assert set(bodies) == {'schedule', 'nowcast', 'prior'}
    assert bodies['schedule']['payload']['consensus'] is None
    assert bodies['schedule']['payload']['actual'] is None and bodies['schedule']['payload']['post_release_surprise'] is None
    assert len(bodies['nowcast']['payload']['nowcast']) == len(bodies['prior']['payload']['prior']) == 4
    assert {item.authority for item in parts} == {'unverified'}
    assert all(item.received_at == item.observed_at == AT and item.content_hash == canonical_hash(json.loads(item.excerpt)) for item in parts)
    assert not any(item.source_id.startswith(('research-run-spot-admission:', 'event-owned-scenario-context:', 'event-opportunity-context:')) for item in request.input_evidence)
    prepare_execution(current, request)
    with case['db'].session() as session:
        bundle = read_research_bundle_in_session(session, request.run_id)
        execution = read_prepared_execution_in_session(session, request.run_id)
        materials = read_perpetual_research_materials_in_session(session, request)
    assert bundle.request == request
    assert materials['candidate_target']['horizon'] == horizon
    assert source.scheduled_at > AT + timedelta(hours=72)
    untrusted = json.loads(execution.prompt.prompt.split('BEGIN_UNTRUSTED_RESEARCH_DATA\n', 1)[1].split('\nEND_UNTRUSTED_RESEARCH_DATA', 1)[0])
    delivered = {row['ref']: row['text'] for row in untrusted}
    originals = materials['review_originals']['originals']
    for item in parts:
        assert delivered['untrusted:evidence:' + item.evidence_id] == item.excerpt
        matching = [row for row in originals if row['source_kind'] == 'frozen_input' and row['role'] == item.evidence_id]
        assert len(matching) == 1 and json.loads(matching[0]['body_json']) == item.model_dump(mode='json')
    print('CPI_PERPETUAL_INPUT_PHASE', horizon, 'actual_entry_factory_root_prompt_review_catalog', flush=True)
