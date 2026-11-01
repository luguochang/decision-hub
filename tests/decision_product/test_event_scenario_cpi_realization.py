"""Publisher-format controlled bodies, real SQL/capture/archive authentication."""

import copy
import hashlib
import importlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence.db import (
    Base,
    RawCaptureRecord,
    SourcePollAttemptRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

MODULE = "packages.kernel.decision_hub_kernel.application.event_scenario_cpi_realization"
URL = "https://www.bls.gov/news.release/archives/cpi_09112025.htm"
T0 = datetime(2025, 9, 11, 12, 30, tzinfo=UTC)
# Controlled transcription of Table A's column structure and published values;
# not a downloaded raw original or a historical point-in-time capture.
HTML = b"""<html>
<body>
<pre>Transmission of material in this release is embargoed until
8:30 a.m. (ET) Thursday, September 11, 2025
CONSUMER PRICE INDEX - AUGUST 2025</pre>
<table id="cpipress1">
<caption>Table A. Percent changes in CPI for All Urban Consumers (CPI-U)</caption>
<thead>
<tr>
<th>Expenditure category</th>
<th colspan="7">Seasonally adjusted changes from preceding month</th>
<th>Unadjusted 12 months ended</th>
</tr>
<tr>
<th>Feb. 2025</th>
<th>Mar. 2025</th>
<th>Apr. 2025</th>
<th>May 2025</th>
<th>Jun. 2025</th>
<th>Jul. 2025</th>
<th>Aug. 2025</th>
</tr>
</thead>
<tbody>
<tr>
<th>All items</th>
<td>0.2</td>
<td>-0.1</td>
<td>0.2</td>
<td>0.1</td>
<td>0.3</td>
<td>0.2</td>
<td>0.4</td>
<td>2.9</td>
</tr>
<tr>
<th>All items less food and energy</th>
<td>0.2</td>
<td>0.1</td>
<td>0.2</td>
<td>0.1</td>
<td>0.2</td>
<td>0.3</td>
<td>0.3</td>
<td>3.1</td>
</tr>
</tbody>
</table>
</body>
</html>"""


def module():
    assert importlib.util.find_spec(MODULE) is not None, "native CPI release reader is missing"
    return importlib.import_module(MODULE)


def inputs():
    event = dict(
        event_key="internal:cpi:2025-08",
        family="cpi",
        stage_id="release",
        scheduled_at="2025-09-11T12:30:00Z",
        schedule_ref={"ref": "schedule", "hash": "1" * 64},
        event_definition_ref={"ref": "definition", "hash": "2" * 64},
        partition_ref={"ref": "partition", "hash": "3" * 64},
    )
    rule = dict(
        schema_version="event-scenario-observation-rule.v1",
        adapter="bls_cpi_release_document.v1",
        source_id="bls-releases",
        adapter_version="official-feed.v2",
        source_url=URL,
        event_key=event["event_key"],
        family="cpi",
        stage_id="release",
        reference_period="2025-08",
        metrics=[
            dict(metric_id=k, unit="percent", reference_period="2025-08", stage_id="release")
            for k in ("headline_mom", "core_mom")
        ],
    )
    event["observation_rule_ref"] = {"ref": "rule", "hash": canonical_hash(rule)}
    return {"event": event}, {"rule": rule}


class Archive:
    def __init__(self):
        self.values = {}

    def add(self, raw):
        digest = hashlib.sha256(raw).hexdigest()
        ref = "raw://sha256/" + digest
        self.values[ref] = raw
        return digest, ref

    def read(self, ref):
        return self.values[ref]


@pytest.fixture
def native(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "cpi.sqlite"))
    Base.metadata.create_all(
        engine, tables=[RawCaptureRecord.__table__, SourcePollAttemptRecord.__table__]
    )
    archive = Archive()
    with Session(engine) as session:

        def seed(*, pubdate=True, article=HTML):
            stamp = "<pubDate>Thu, 11 Sep 2025 08:30:00 -0400</pubDate>" if pubdate else ""
            rss = (
                '<rss version="2.0"><channel><title>BLS</title><item><guid>cpi-2025-08</guid>'
                "<title>Consumer Price Index - August 2025</title><link>"
                + URL
                + "</link>"
                + stamp
                + "</item></channel></rss>"
            ).encode()
            feed_hash, feed_ref = archive.add(rss)
            article_hash, article_ref = archive.add(article)
            session.add(
                SourcePollAttemptRecord(
                    attempt_id="attempt",
                    source_id="bls-releases",
                    trigger_type="scheduled",
                    status="succeeded",
                    started_at=T0,
                    completed_at=T0 + timedelta(seconds=4),
                    received_count=1,
                    content_snapshot_json=json.dumps(
                        dict(
                            http_status=200,
                            parser_version="official-feed.v2",
                            content_hash=feed_hash,
                            parse_status="valid",
                            parsed_count=1,
                        )
                    ),
                )
            )
            for name, url, raw, digest, ref, start, end in (
                ("rss", "https://www.bls.gov/feed/bls_latest.rss", rss, feed_hash, feed_ref, 0, 1),
                ("article", URL, article, article_hash, article_ref, 1, 2),
            ):
                session.add(
                    RawCaptureRecord(
                        capture_id=name,
                        context_id="source:attempt",
                        url=url,
                        adapter_version="official-feed.v2",
                        status_code=200,
                        error_code=None,
                        body_hash=digest,
                        body_ref=ref,
                        byte_count=len(raw),
                        media_type="text/xml" if name == "rss" else "text/html",
                        started_at=T0 + timedelta(seconds=start),
                        completed_at=T0 + timedelta(seconds=end),
                        recorded_at=T0 + timedelta(seconds=3),
                    )
                )
            session.commit()

        yield session, archive, seed
    engine.dispose()


def read(native):
    session, archive, _ = native
    plan, originals = inputs()
    return module().read_cpi_event_scenario_actual_inputs_in_session(
        session,
        plan=plan,
        originals=originals,
        cutoff_at=T0 + timedelta(seconds=5),
        archive=archive,
    )


def test_native_cpi_reader_maps_official_rss_and_html_to_internal_event(native):
    native[2]()
    result = read(native)
    assert result["realization"]["event"]["event_key"] == "internal:cpi:2025-08"
    assert {x["metric_id"]: x["value"] for x in result["realization"]["metrics"]} == {
        "headline_mom": "0.4",
        "core_mom": "0.3",
    }
    assert result["realization"]["public_at"] == "2025-09-11T12:30:00Z"
    assert result["realization"]["received_at"] == "2025-09-11T12:30:02Z"
    assert result["realization"]["known_at"] == "2025-09-11T12:30:04Z"
    assert result["source_evidence"]["publication_clock_basis"] == "official_rss_pubDate"


def test_cpi_missing_publisher_clock_stays_pending_not_scheduled_at(native):
    native[2](pubdate=False)
    assert read(native) is None


def test_cpi_other_reference_month_stays_pending(native):
    native[2](article=HTML.replace(b"AUGUST 2025", b"JULY 2025"))
    assert read(native) is None


def test_cpi_table_must_bind_the_selected_monthly_column_not_another_month(native):
    shifted = HTML.replace(b"Aug. 2025", b"Sep. 2025").replace(b"Jul. 2025", b"Aug. 2025")
    assert shifted != HTML
    native[2](article=shifted)
    assert read(native) is None


def test_cpi_annual_first_layout_cannot_shift_the_selected_sa_month(native):
    soup = BeautifulSoup(HTML, "html.parser")
    headers = soup.thead.tr.find_all("th", recursive=False)
    headers[0].insert_after(headers[-1].extract())
    for row in soup.tbody.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        cells[0].insert_after(cells[-1].extract())
    native[2](article=str(soup).encode())
    assert read(native) is None


def test_cpi_modified_article_bytes_cannot_reuse_native_receipt(native):
    native[2]()
    row = native[0].get(RawCaptureRecord, "article")
    native[1].values[row.body_ref] = HTML.replace(b"0.4", b"0.9")
    with pytest.raises(ValueError, match="cpi_release_capture_original_invalid"):
        read(native)


def test_cpi_rule_cannot_borrow_another_family_or_units(native):
    native[2]()
    plan, originals = inputs()
    originals = copy.deepcopy(originals)
    originals["rule"]["metrics"][0]["unit"] = "fraction"
    plan["event"]["observation_rule_ref"]["hash"] = canonical_hash(originals["rule"])
    with pytest.raises(ValueError, match="cpi_release_rule_invalid"):
        module().read_cpi_event_scenario_actual_inputs_in_session(
            native[0],
            plan=plan,
            originals=originals,
            cutoff_at=T0 + timedelta(seconds=5),
            archive=native[1],
        )
