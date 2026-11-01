"""Real CPI HTML/JSON parsing reuse; provenance and native reads stay fresh."""
from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.ports import (
    cpi_nowcast_history as history,
    cpi_pdf_parse_cache as cache,
    cpi_schedule as schedule,
)


def calendar(year=2026, *, discriminator=""):
    # Tuesday is deliberately checked by the actual native FRED parser.
    return (
        f"<h1>{year} Economic Release Calendar - Consumer Price Index</h1>"
        "<p>All times are US Central Time.</p>"
        "<table><tr><td>Tuesday November 10, 2026</td></tr>"
        "<tr><td>7:30 AM</td><td>Consumer Price Index</td></tr></table>"
        f"<!-- {discriminator} -->"
    ).encode()


def history_body(axis, *, discriminator=""):
    names = ("CPI Inflation", "Core CPI Inflation", "Actual CPI Inflation", "Actual Core CPI Inflation")
    return json.dumps([{
        "chart": {"caption": "Inflation Nowcasting", "subcaption": "2026-01", "yaxisname": axis},
        "categories": [{"category": [{"label": "02/01"}]}],
        "dataset": [{"seriesname": name, "data": [{
            "value": str(index + 1), "tooltext": name + "{br}02/01{br}",
        }]} for index, name in enumerate(names)],
        "diagnostic": discriminator,
    }]).encode()


def bodies(*, discriminator=""):
    return (
        history_body("Month-over-month percent change", discriminator=discriminator),
        history_body("Year-over-year percent change", discriminator=discriminator),
    )


@pytest.fixture(autouse=True)
def empty_cache():
    cache._cached_parse.cache_clear()
    yield
    cache._cached_parse.cache_clear()


def test_fred_repeated_actual_html_parses_once(monkeypatch):
    calls = []
    original = schedule._Tables.feed

    def counted(parser, text):
        calls.append(1)
        return original(parser, text)

    monkeypatch.setattr(schedule._Tables, "feed", counted)
    body = calendar()
    first = schedule.parse_fred_cpi_calendar(body, year=2026)
    second = schedule.parse_fred_cpi_calendar(body, year=2026)
    assert first == second
    assert first[0][2] == datetime(2026, 11, 10, 13, 30, tzinfo=UTC)
    print("FRED_ACTUAL_FEED_CALLS=" + str(len(calls)))
    assert len(calls) == 1
    schedule.parse_fred_cpi_calendar(calendar(discriminator="new bytes"), year=2026)
    assert len(calls) == 2
    before = cache._cached_parse.cache_info().currsize
    for _ in range(2):
        with pytest.raises(ValueError, match="identity_or_timezone"):
            schedule.parse_fred_cpi_calendar(body, year=2027)
    assert len(calls) == 4 and cache._cached_parse.cache_info().currsize == before

    old_parser = schedule._parse_fred_cpi_calendar

    def different_parser(value, year):
        return old_parser(value, year)

    monkeypatch.setattr(schedule, "_parse_fred_cpi_calendar", different_parser)
    schedule.parse_fred_cpi_calendar(body, year=2026)
    assert len(calls) == 5

    def changed_code(parser, text):
        calls.append(2)
        return original(parser, text)

    # Same helper object with changed loaded code, not only a new alias.
    monkeypatch.setattr(counted, "__code__", changed_code.__code__)
    schedule.parse_fred_cpi_calendar(body, year=2026)
    assert len(calls) == 6
    monkeypatch.setattr(schedule, "CPI_SCHEDULE_VERSION", "test-parser-revision")
    schedule.parse_fred_cpi_calendar(body, year=2026)
    assert len(calls) == 7


def test_history_repeated_actual_json_parses_once(monkeypatch):
    calls = []
    original = history._records

    def counted(body, axis):
        calls.append(axis)
        return original(body, axis)

    monkeypatch.setattr(history, "_records", counted)
    first = history.parse_cpi_history(*bodies())
    second = history.parse_cpi_history(*bodies())
    assert first == second
    print("HISTORY_ACTUAL_RECORD_CALLS=" + str(len(calls)))
    assert len(calls) == 2  # One actual monthly + one actual yearly parse.
    first[0]["2026-01"][1].clear()
    first[0]["2026-01"][2]["CPI Inflation"].clear()
    assert history.parse_cpi_history(*bodies()) == second
    assert len(calls) == 2
    month, year = bodies()
    changed_month, changed_year = bodies(discriminator="new bytes")
    history.parse_cpi_history(changed_month, year)
    history.parse_cpi_history(month, changed_year)
    assert len(calls) == 6
    monkeypatch.setattr(history, "VERSION", "test-history-revision")
    history.parse_cpi_history(month, year)
    assert len(calls) == 8

    old = history._records

    def changed_records(value, axis):
        return old(value, axis)

    monkeypatch.setattr(history, "_records", changed_records)
    history.parse_cpi_history(month, year)
    assert len(calls) == 10
    actual_loads = history.json.loads

    def changed_loads(*args, **kwargs):
        return actual_loads(*args, **kwargs)

    monkeypatch.setattr(history.json, "loads", changed_loads)
    history.parse_cpi_history(month, year)
    assert len(calls) == 12
    before = cache._cached_parse.cache_info().currsize
    for _ in range(2):
        with pytest.raises(ValueError, match="chart_empty"):
            history.parse_cpi_history(b"[]", year)
    assert len(calls) == 14 and cache._cached_parse.cache_info().currsize == before


def test_fred_float_year_cannot_borrow_warm_integer_entry():
    body = calendar()
    assert schedule.parse_fred_cpi_calendar(body, year=2026)
    with pytest.raises(ValueError, match="identity_or_timezone"):
        schedule.parse_fred_cpi_calendar(body, year=2026.0)


@pytest.mark.parametrize("limit", ["MAX_BODY_BYTES", "MAX_RESULT_BYTES", "MAX_RESULT_NODES"])
def test_oversized_cache_input_or_result_still_returns_actual_history(monkeypatch, limit):
    expected = history.parse_cpi_history(*bodies())
    cache._cached_parse.cache_clear()
    monkeypatch.setattr(cache, limit, 1)
    calls = []
    original = history._records

    def counted(body, axis):
        calls.append(axis)
        return original(body, axis)

    monkeypatch.setattr(history, "_records", counted)
    assert history.parse_cpi_history(*bodies()) == expected
    assert history.parse_cpi_history(*bodies()) == expected
    assert len(calls) == 4 and cache._cached_parse.cache_info().currsize == 0


def test_html_history_share_four_entries_and_pdf_limits_stay_original():
    schedule.parse_fred_cpi_calendar(calendar(), year=2026)
    for index in range(3):
        history.parse_cpi_history(*bodies(discriminator=str(index)))
    before = cache._cached_parse.cache_info()
    assert before.maxsize == before.currsize == 4
    history.parse_cpi_history(*bodies(discriminator="four"))
    schedule.parse_fred_cpi_calendar(calendar(), year=2026)
    assert cache._cached_parse.cache_info().misses == before.misses + 2
    assert cache.MAX_BODY_BYTES * before.maxsize == 20 * 1024 * 1024
    assert cache.MAX_RESULT_BYTES * before.maxsize == 8 * 1024 * 1024
    assert cache.MAX_RESULT_NODES == 100_000
    with pytest.raises(ValueError, match="cpi_calendar_body_too_large"):
        schedule.parse_fred_cpi_calendar(b"x" * (2 * 1024 * 1024 + 1), year=2026)
    with pytest.raises(ValueError, match="cpi_dol_pdf_invalid"):
        schedule.parse_dol_next_cpi_release(b"%PDF-" + b"x" * cache.MAX_BODY_BYTES)


def test_warm_calendar_cache_keeps_real_sqlite_original_hash_pit_and_provenance(tmp_path, monkeypatch):
    from packages.kernel.decision_hub_kernel.application.cpi_schedule_original import (
        read_cpi_schedule_original_in_session,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import (
        Database, ObservationRecord, RawCaptureRecord, SourcePollAttemptRecord,
    )
    from packages.provider_adapters.research.raw_capture import RawBodyArchive, safe_url

    database = Database("sqlite:///" + (tmp_path / "original.sqlite3").as_posix())
    database.create_all()
    archive = RawBodyArchive(tmp_path / "raw")
    body = calendar()
    digest, ref = archive.put(body)
    start = datetime(2026, 8, 13, tzinfo=UTC)
    received, known = start + timedelta(seconds=1), start + timedelta(seconds=2)
    capture = dict(capture_id="fred", raw_body_hash=digest, raw_body_ref=ref,
                   url=schedule.fred_calendar_url(2026), received_at=received)
    payload = schedule.parse_cpi_publication_schedules(
        body, None, year=2026, fred_capture=capture, dol_capture=None, attempt_id="poll",
    )[0]
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True,
                     ensure_ascii=False, separators=(",", ":"))
    with database.session() as session:
        session.add(SourcePollAttemptRecord(
            attempt_id="poll", source_id=schedule.CPI_SCHEDULE_SOURCE_ID,
            trigger_type="manual", status="succeeded", started_at=start,
            completed_at=known, received_count=1, content_snapshot_json=json.dumps({
                "http_status": 200, "content_hash": digest,
                "parser_version": schedule.CPI_SCHEDULE_VERSION,
                "parse_status": "valid", "parsed_count": 1,
            }),
        ))
        session.add(RawCaptureRecord(
            capture_id="fred", context_id="source:poll", url=safe_url(capture["url"]),
            status_code=200, body_hash=digest, body_ref=ref, byte_count=len(body),
            media_type="text/html", adapter_version=schedule.CPI_SCHEDULE_VERSION,
            error_code=None, started_at=start, completed_at=received, recorded_at=received,
        ))
        session.add(ObservationRecord(
            observation_id="schedule", event_id="calendar", source_id=schedule.CPI_SCHEDULE_SOURCE_ID,
            source_type="official_feed", observed_at=received, published_at=None,
            received_at=received, language="en", text=raw,
            content_hash=hashlib.sha256(raw.encode()).hexdigest(), source_url=capture["url"],
            event_hint=payload.event_key, scheduled_at=payload.scheduled_at,
        ))
    reads, parses = [], []
    read, feed = archive.read, schedule._Tables.feed

    def read_actual(value):
        reads.append(value)
        return read(value)

    def feed_actual(parser, text):
        parses.append(1)
        return feed(parser, text)

    monkeypatch.setattr(archive, "read", read_actual)
    monkeypatch.setattr(schedule._Tables, "feed", feed_actual)

    def original(cutoff=known):
        with database.session() as session:
            return read_cpi_schedule_original_in_session(
                session, "schedule", archive=archive, cutoff_at=cutoff)

    try:
        assert original().payload == original().payload == payload
        assert len(reads) == 2 and len(parses) == 1
        with pytest.raises(ValueError, match="cpi_schedule_pit_invalid"):
            original(known - timedelta(microseconds=1))
        with database.session() as session:
            session.get(RawCaptureRecord, "fred").context_id = "source:other"
        with pytest.raises(ValueError, match="cpi_schedule_capture_binding_invalid"):
            original()
        with database.session() as session:
            session.get(RawCaptureRecord, "fred").context_id = "source:poll"
        (archive.root / digest[:2] / digest).write_bytes(b"X" + body[1:])
        with pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
            original()
        assert len(parses) == 1 and len(reads) == 3
    finally:
        database.engine.dispose()
