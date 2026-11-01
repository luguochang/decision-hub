"""Read-only architecture audit. Writes only its output directory; never the ledger.

Run from repository root with the project Python. --network additionally fetches
public official calendars and replays the existing parser without ingesting data.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def database_snapshot(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Not an existing nonempty ledger: {path}")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {t: db.execute('SELECT count(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0] for t in tables}
        queries = {
            "run_groups": "SELECT admission_origin,strategy_version,status,count(*) n FROM runs GROUP BY 1,2,3 ORDER BY 1,2,3",
            "automatic_research": "SELECT run_id,status,error_code,created_at FROM runs WHERE admission_origin='automatic' AND strategy_version='research.v1' ORDER BY created_at",
            "review_groups": "SELECT status,failure_code,count(*) n FROM review_schedules GROUP BY 1,2",
            "window_groups": "SELECT status,count(*) n,min(target_at) first_target,max(target_at) last_target FROM event_window_samples GROUP BY status",
            "outbox_groups": "SELECT channel,CASE WHEN sent_at IS NOT NULL THEN 'sent' WHEN failed_at IS NOT NULL THEN 'failed' ELSE 'pending' END status,count(*) n FROM outbox GROUP BY 1,2",
            "forecast_groups": "SELECT horizon,direction,count(*) n FROM forecasts GROUP BY 1,2",
            "outcome_groups": "SELECT quality_status,count(*) n FROM outcomes GROUP BY 1",
            "macro_events": "SELECT macro_event_id,source_id,family,scheduled_at,status FROM macro_events ORDER BY scheduled_at",
            "evidence_lineage": "SELECT count(*) n,count(acquisition_receipt_id) with_receipt,count(content_ref) with_content_ref,count(DISTINCT content_hash) distinct_content_hashes FROM research_evidence",
            "evidence_quality": "SELECT quality,freshness_status,count(*) n FROM research_evidence GROUP BY 1,2",
            "facts_quality": "SELECT quality,count(*) n FROM research_facts GROUP BY 1",
            "orphan_facts": "SELECT count(*) n FROM research_facts f LEFT JOIN research_evidence e ON f.evidence_id=e.evidence_id WHERE e.evidence_id IS NULL",
            "cross_run_fact_links": "SELECT count(*) n FROM research_facts f JOIN research_evidence e ON f.evidence_id=e.evidence_id WHERE f.run_id<>e.run_id",
            "data_jobs": "SELECT status,count(*) n FROM data_jobs GROUP BY status",
            "document_adapters": "SELECT correction_model_ref,quality_status,count(*) n FROM research_document_corrections GROUP BY 1,2",
        }
        result = {"captured_at": datetime.now(UTC).isoformat(), "database": str(path.resolve()), "sqlite_runtime": sqlite3.sqlite_version, "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0], "counts": counts, "queries": {}, "query_errors": {}}
        for name, sql in queries.items():
            try:
                result["queries"][name] = [dict(r) for r in db.execute(sql)]
            except sqlite3.Error as exc:
                result["query_errors"][name] = str(exc)
        db.rollback()
    return result


async def parser_probes(output: Path, network: bool) -> dict:
    import httpx
    from packages.source_adapters.official_feeds.html_calendar import OfficialHtmlCalendarSource, parse_official_schedule_html
    from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest

    year = datetime.now(UTC).year + 2
    initial = f"<h2>{year}</h2><p>November 15</p>"
    body = initial
    async def fetcher(_url):
        return 200, body
    source = OfficialHtmlCalendarSource(SourceManifest(source_id="audit-calendar", source_type="official_feed", version="audit.v1", authority_level="official", max_batch=20), "https://example.invalid/schedule", family="FOMC", event_title="Meeting", fetcher=fetcher)
    first = await source.poll()
    body = f"<h2>{year}</h2><p>October 15</p><p>November 15 revised agenda</p>"
    second = await source.poll(first.cursor_after)
    split = parse_official_schedule_html(f'<h2>{year} Meetings</h2><div class="month">October</div><div class="date">27-28</div>', source_id="audit", family="FOMC", event_title="Meeting")
    result = {"probe_time": datetime.now(UTC).isoformat(), "cursor_insert_and_revision": {"first_emitted": len(first.envelopes), "expected_second_emitted": 2, "actual_second_emitted": len(second.envelopes), "defect_reproduced": len(second.envelopes) == 0, "cursor": first.cursor_after}, "split_month_and_day": {"expected_meetings": 1, "parsed_meetings": len(split)}, "official_pages": {}}
    if network:
        urls = {"fed-calendar": ("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "FOMC"), "boj-calendar": ("https://www.boj.or.jp/en/mopo/mpmsche_minu/", "BOJ")}
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            for source_id, (url, family) in urls.items():
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    target = output / f"{source_id}.html"
                    target.write_text(response.text, encoding="utf-8")
                    parsed = parse_official_schedule_html(response.text, source_id=source_id, family=family, event_title=family)
                    result["official_pages"][source_id] = {"url": url, "fetched_at": datetime.now(UTC).isoformat(), "status": response.status_code, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "parsed_count": len(parsed), "parsed": [{"id": uid, "scheduled_at": dt.isoformat(), "text": title} for uid, dt, title in parsed]}
                except Exception as exc:
                    result["official_pages"][source_id] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
    return result


def api_probes(base: str) -> dict:
    import httpx
    result = {}
    # Local API must not be sent through the shell's outbound HTTP proxy.
    with httpx.Client(timeout=12, trust_env=False) as client:
        for route in ("/health/ready", "/v1/health", "/v1/sources", "/v1/macro-events", "/v1/source-acquisitions"):
            try:
                response = client.get(base.rstrip("/") + route)
                result[route] = {"status": response.status_code, "content_type": response.headers.get("content-type")}
                result[route]["body"] = response.json()
            except Exception as exc:
                result[route] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "data/core-live-20260913/db/decision_hub.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8047")
    parser.add_argument("--network", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"scope": "Read-only DB snapshot, GET probes, pure parser reproductions; no ingestion or new model run", "database": database_snapshot(args.database), "parser": asyncio.run(parser_probes(args.output, args.network)), "api": api_probes(args.api)}
    destination = args.output / "snapshot.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(destination.resolve()), "time": result["database"]["captured_at"], "query_errors": result["database"]["query_errors"], "parser_reproduced": result["parser"]["cursor_insert_and_revision"], "api_status": {k: v.get("status", v.get("error")) for k, v in result["api"].items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
