"""Pure CPI parsing cache: real tiny PDFs, no original DB/Host/model calls."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from packages.kernel.decision_hub_kernel.ports import (
    cpi_pdf_parse_cache as cache,
)
from packages.kernel.decision_hub_kernel.ports import (
    cpi_release_history as history,
)
from packages.kernel.decision_hub_kernel.ports import (
    cpi_schedule as schedule,
)

HISTORY_TEXT = (
    "Transmission of material in this release is embargoed until "
    "8:30 a.m. (ET) Wednesday, August 12, 2026 "
    "CONSUMER PRICE INDEX - JULY 2026"
)
SCHEDULE_TEXT = (
    "The Consumer Price Index news release for October 2026 is scheduled to "
    "be published on Tuesday, November 10, 2026, at 8:30 a.m. (ET)."
)


def pdf(*texts: str, discriminator: str = "") -> bytes:
    writer = PdfWriter()
    writer.add_metadata({"/Subject": discriminator})
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(("BT /F1 12 Tf 20 20 Td (" + escaped + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def provenance(capture: str, received: datetime) -> dict:
    return {
        "document_url": history.DOCUMENTS[0],
        "capture_id": capture,
        "attempt_id": "attempt-" + capture,
        "received_at": received,
    }


@pytest.fixture(autouse=True)
def empty_cache():
    cache._cached_parse.cache_clear()
    yield
    cache._cached_parse.cache_clear()


def test_history_uses_actual_bytes_but_rebuilds_provenance_and_parser_identity(monkeypatch):
    calls = []

    def counted(stream):
        calls.append(1)
        return PdfReader(stream)

    monkeypatch.setattr(history, "PdfReader", counted)
    body = pdf(HISTORY_TEXT)
    received = datetime(2026, 8, 13, tzinfo=UTC)
    first = history.parse_cpi_release_history(body, provenance=provenance("one", received))
    second = history.parse_cpi_release_history(
        body, provenance=provenance("two", received + timedelta(days=1))
    )
    assert len(calls) == 1
    assert first is not second and first.capture_id != second.capture_id
    assert first.received_at != second.received_at and first.attempt_id != second.attempt_id
    assert first.raw_body_hash == second.raw_body_hash
    assert (first.reference_period, first.scheduled_at) == (
        "2026-07",
        datetime(2026, 8, 12, 12, 30, tzinfo=UTC),
    )
    # A cache hit cannot admit an invalid current provenance.
    with pytest.raises(ValueError, match="cpi_release_history_identity_invalid"):
        history.parse_cpi_release_history(
            body, provenance=provenance("bad-clock", datetime(2026, 8, 1, tzinfo=UTC))
        )
    assert len(calls) == 1
    different_raw = pdf(HISTORY_TEXT, discriminator="same-text-different-raw")
    changed = history.parse_cpi_release_history(
        different_raw, provenance=provenance("three", received)
    )
    assert len(calls) == 2 and changed.raw_body_hash != first.raw_body_hash
    # Same visible parser version, a genuinely different loaded pure callable.
    old = history._parse_cpi_release_header

    def replacement(value):
        return old(value)

    monkeypatch.setattr(history, "_parse_cpi_release_header", replacement)
    history.parse_cpi_release_history(body, provenance=provenance("four", received))
    assert len(calls) == 3

    # A dependency identity changes independently of pure parser/body/version.
    def reader_replacement(stream):
        calls.append(1)
        return PdfReader(stream)

    monkeypatch.setattr(history, "PdfReader", reader_replacement)
    history.parse_cpi_release_history(body, provenance=provenance("five", received))
    assert len(calls) == 4


def test_schedule_scans_all_pages_shared_four_entry_lru_and_version_misses(monkeypatch):
    calls = []

    def counted(stream):
        calls.append(1)
        return PdfReader(stream)

    monkeypatch.setattr(schedule, "PdfReader", counted)
    bodies = [pdf("Unrelated cover", SCHEDULE_TEXT, discriminator=str(i)) for i in range(5)]
    expected = ("2026-10", datetime(2026, 11, 10, 13, 30, tzinfo=UTC), 1)
    assert schedule.parse_dol_next_cpi_release(bodies[0]) == expected
    assert schedule.parse_dol_next_cpi_release(bodies[0]) == expected
    assert len(calls) == 1
    for body in bodies[1:]:
        assert schedule.parse_dol_next_cpi_release(body) == expected
    assert len(calls) == 5
    assert cache._cached_parse.cache_info().maxsize == 4
    assert cache._cached_parse.cache_info().currsize == 4
    schedule.parse_dol_next_cpi_release(bodies[0])
    assert len(calls) == 6  # Oldest original bytes really evicted.
    history.parse_cpi_release_history(
        pdf(HISTORY_TEXT), provenance=provenance("shared", datetime(2026, 8, 13, tzinfo=UTC))
    )
    assert cache._cached_parse.cache_info().currsize == 4  # Not two independent four-body caches.
    monkeypatch.setattr(schedule, "CPI_SCHEDULE_VERSION", "cache-test-revision")
    assert schedule.parse_dol_next_cpi_release(bodies[0]) == expected and len(calls) == 7
    monkeypatch.setattr(cache.pypdf, "__version__", "cache-test-dependency-revision")
    assert schedule.parse_dol_next_cpi_release(bodies[0]) == expected and len(calls) == 8
    before = cache._cached_parse.cache_info()
    with pytest.raises(ValueError, match="cpi_dol_pdf_invalid"):
        schedule.parse_dol_next_cpi_release(b"%PDF-" + b"x" * cache.MAX_BODY_BYTES)
    assert cache._cached_parse.cache_info() == before
    assert before.maxsize * cache.MAX_BODY_BYTES == 20 * 1024 * 1024
