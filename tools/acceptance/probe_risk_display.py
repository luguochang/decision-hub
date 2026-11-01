"""Thirty labelled isolated alerts; only the actual visible PC writes display receipts.

No HTTP, models, migrations, notification dispatch, display POST or ACK POST here.
Root runs inject-next once, opens that alert in the real PC, then runs observe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import socket
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
B1 = REPO / "artifacts/market-risk-implementation-20261007/B1"
DATABASE = B1 / "O05-real01/live.sqlite3"
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"
TABLES = {
    "risk_monitor_plans", "risk_condition_evaluations", "risk_alerts",
    "risk_monitor_commands", "risk_alert_delivery_receipts", "outbox",
}
WRITABLE = TABLES - {"risk_alert_delivery_receipts"}
SOURCE_FILES = (
    "packages/kernel/decision_hub_kernel/application/risk_monitor.py",
    "packages/kernel/decision_hub_kernel/ports/risk_monitor.py",
    "packages/kernel/decision_hub_kernel/persistence/risk_records.py",
    "packages/kernel/decision_hub_kernel/application/outbox.py",
    "packages/query_views/market_risk.py",
    "apps/decision-desk/src/product/RiskDashboard.tsx",
    "apps/decision-desk/src/api/risk.ts",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def now() -> datetime:
    return datetime.now(UTC)


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # Only read the named internal SQLite UTC storage columns this way.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def guard(args) -> tuple[Path, dict]:
    db, output = args.database.resolve(), args.output.resolve()
    if db != DATABASE.resolve() or not db.is_file():
        raise ValueError("only_named_O05_isolated_database_allowed")
    if output.parent != B1.resolve() or not output.name.startswith("O03-"):
        raise ValueError("output_must_be_direct_B1_O03_namespace")
    output.mkdir(exist_ok=True)
    pins = json.loads(args.source_plan.read_text(encoding="utf-8"))
    if pins["database"] != str(DATABASE.resolve()):
        raise ValueError("source_plan_database_mismatch")
    for relative, expected in pins["sources"].items():
        if relative not in SOURCE_FILES or sha(REPO / relative) != expected:
            raise ValueError("source_changed:" + relative)
    if set(pins["sources"]) != set(SOURCE_FILES):
        raise ValueError("source_plan_not_closed")
    return output, pins


def sql_authorizer(writable: bool):
    writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
    forbidden = {
        sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER,
    }
    def authorize(action, first, _second, _db, _source):
        if action in forbidden:
            return sqlite3.SQLITE_DENY
        if action in writes and (not writable or first not in WRITABLE):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and first not in TABLES | {"sqlite_master"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return authorize


def read_alert(db: Path, alert_id: str) -> dict:
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.set_authorizer(sql_authorizer(False))
        conn.execute("BEGIN")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM risk_alerts WHERE alert_id=?", (alert_id,)).fetchone()
        if row is None:
            raise ValueError("fixture_alert_missing")
        body = json.loads(row["payload_json"])
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(canonical.encode()).hexdigest() != row["payload_hash"]:
            raise ValueError("fixture_alert_hash_invalid")
        records = [dict(item) for item in conn.execute(
            "SELECT * FROM risk_alert_delivery_receipts WHERE alert_id=? ORDER BY received_at",
            (alert_id,),
        )]
        for item in records:
            receipt = json.loads(item["payload_json"])
            if (receipt["receipt_id"] != item["receipt_id"]
                    or receipt["alert_id"] != alert_id
                    or receipt["kind"] != item["receipt_kind"]
                    or instant(receipt["received_at"]) != instant(item["received_at"])):
                raise ValueError("server_receipt_row_mismatch")
        if conn.total_changes != 0 or not conn.in_transaction:
            raise ValueError("read_only_snapshot_changed")
        return {"alert": dict(row), "payload": body, "server_receipts": records}
    finally:
        conn.close()


def inject(args, output: Path) -> dict:
    from decimal import Decimal
    from sqlalchemy import event
    from packages.kernel.decision_hub_kernel.application.risk_monitor import (
        RiskMonitorService, content_hash,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    from packages.kernel.decision_hub_kernel.ports.risk_monitor import (
        RiskComparisonV1, RiskConditionV1, RiskMetricV1, RiskMonitorInput,
    )

    indices = sorted(int(p.stem.split("-")[1]) for p in output.glob("trigger-*.json"))
    index = len(indices) + 1
    if indices != list(range(1, index)) or index > 30:
        raise ValueError("finite_sequence_not_next_or_already_30")
    started, known = now(), now()
    data_as_of = known.replace(second=0, microsecond=0)
    ref = f"fixture://isolated_delivery_probe/{output.name}/{index:02d}"
    fixture = {
        "fixture": True, "financial_score_eligible": False, "index": index,
        "input_ref": ref, "source_known_at": known.isoformat(),
        "data_as_of": data_as_of.isoformat(), "started_at": started.isoformat(),
        "scope_id": SCOPE, "close_return": "-0.016", "window_seconds": 1800,
    }
    dump(output / f"fixture-{index:02d}.json", fixture)
    database = Database(f"sqlite+pysqlite:///{DATABASE.as_posix()}")
    @event.listens_for(database.engine, "connect")
    def constrain(conn, _record):
        conn.set_authorizer(sql_authorizer(True))
    monitor = RiskMonitorService(database)
    try:
        rule = RiskConditionV1(
            condition_id=f"fixture-o03-{output.name}-{index:02d}", scope_id=SCOPE,
            expression=RiskComparisonV1(left=RiskMetricV1(), right=Decimal("0.015")),
        )
        plan_id = monitor.ensure_plan(rule, expires_at=known + timedelta(hours=1))
        claim = monitor.claim(plan_id)
        if claim is None:
            raise ValueError("actual_monitor_claim_unavailable_no_fake_claim")
        sample = RiskMonitorInput(
            scope_id=SCOPE, as_of=claim.as_of, data_as_of=data_as_of,
            window_seconds=1800, close_return=Decimal("-0.016"), data_state="fresh",
            input_ref=ref, input_hash=content_hash(fixture),
            manifest_hash=content_hash({"fixture_schema": "o03.isolated-input.v1"}),
        )
        if not monitor.complete(claim, sample):
            raise ValueError("actual_monitor_completion_fenced")
        with database.session() as session:
            from packages.kernel.decision_hub_kernel.persistence.risk_records import RiskAlertRecord
            row = session.query(RiskAlertRecord).filter_by(plan_id=plan_id).one()
            alert_id = row.alert_id
        pause = monitor.command(plan_id, "pause", request_id=f"o03-pause-{output.name}-{index}")
        stored = read_alert(DATABASE, alert_id)
        if (stored["payload"]["input"]["input_ref"] != ref
                or stored["server_receipts"]):
            raise ValueError("new_fixture_identity_or_receipt_invalid")
        result = {**fixture, "plan_id": plan_id, "alert_id": alert_id,
                  "evaluation_as_of": claim.as_of.isoformat(), "paused": pause,
                  "published_at": stored["alert"]["published_at"],
                  "payload_hash": stored["alert"]["payload_hash"],
                  "completed_at": now().isoformat(), "display_receipt_written_by_script": False}
        dump(output / f"trigger-{index:02d}.json", result)
        return result
    finally:
        database.engine.dispose()


def observe(args, output: Path) -> dict:
    trigger = json.loads((output / f"trigger-{args.index:02d}.json").read_text(encoding="utf-8"))
    stored = read_alert(DATABASE, trigger["alert_id"])
    if (stored["alert"]["payload_hash"] != trigger["payload_hash"]
            or stored["payload"]["input"]["input_ref"] != trigger["input_ref"]):
        raise ValueError("fixture_original_changed")
    witness = json.loads(args.witness.read_text(encoding="utf-8")) if args.witness else None
    visible = bool(witness and witness.get("alert_id") == trigger["alert_id"]
                   and witness.get("document_visibility") == "visible"
                   and witness.get("detail_visible") is True
                   and witness.get("observer") == "isolated_test_observer"
                   and witness.get("url", "").startswith("http://127.0.0.1:5174/")
                   and witness.get("observed_at"))
    if visible:
        # The browser witness is external UTC evidence; unlike SQLite storage,
        # no missing offset is supplied on its behalf.
        observed = datetime.fromisoformat(witness["observed_at"].replace("Z", "+00:00"))
        visible = observed.tzinfo is not None and observed >= instant(trigger["source_known_at"])
    displays = [r for r in stored["server_receipts"] if r["receipt_kind"] == "display"]
    acks = [r for r in stored["server_receipts"] if r["receipt_kind"] == "ack"]
    received = displays[0]["received_at"] if displays else None
    latency = (instant(received) - instant(trigger["source_known_at"])).total_seconds() if received else None
    result = {"index": args.index, "alert_id": trigger["alert_id"],
              "observed_at": now().isoformat(), "source_known_at": trigger["source_known_at"],
              "data_as_of": trigger["data_as_of"], "published_at": trigger["published_at"],
              "server_display_received_at": received, "display_latency_seconds": latency,
              "browser_visible_witness": witness, "browser_visible_verified": visible,
              "witness_sha256": sha(args.witness) if args.witness else None,
              "server_display_receipts": displays, "server_ack_receipts": acks,
              "ack_is_separate": True, "qualified": visible and latency is not None and 0 <= latency <= 120}
    with (output / "observations.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


def summary(output: Path) -> dict:
    observations = {}
    log = output / "observations.jsonl"
    for line in log.read_text(encoding="utf-8").splitlines() if log.exists() else ():
        row = json.loads(line)
        observations[row["index"]] = row
    triggers = {int(p.stem.split("-")[1]) for p in output.glob("trigger-*.json")}
    rows = [observations.get(i, {"index": i, "qualified": False}) for i in range(1, 31)]
    measured = sorted(r["display_latency_seconds"] for r in rows
                      if r.get("browser_visible_verified") and r.get("display_latency_seconds") is not None)
    p95 = measured[math.ceil(.95 * len(measured)) - 1] if measured else None
    unknown = [r["index"] for r in rows if not r.get("browser_visible_verified")
               or r.get("display_latency_seconds") is None]
    failures = [r["index"] for r in rows if r.get("browser_visible_verified")
                and r.get("display_latency_seconds") is not None and not r["qualified"]]
    result = {"schema_version": "risk-pc-display-isolated-probe.v1", "requested": 30,
              "triggered": len(triggers), "measured_visible": len(measured),
              "unknown_indices": unknown, "failed_indices": failures, "p95_seconds": p95,
              "isolated_engineering_pass": triggers == set(range(1, 31)) and not unknown
              and not failures and p95 is not None and p95 <= 120,
              "real_market_display_acceptance": False, "financial_score_eligible": False,
              "observer": "isolated_test_observer", "formal_writes": 0, "model_calls": 0,
              "display_or_ack_api_calls_by_script": 0, "rows": rows}
    dump(output / "summary01.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inject-next", "observe", "summary"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--index", type=int, choices=range(1, 31))
    parser.add_argument("--witness", type=Path)
    args = parser.parse_args()
    output, _pins = guard(args)
    os.environ["DECISION_HUB_DATA_DIR"] = str(output / "unused-private-bootstrap")
    def no_network(*_args, **_kwargs):
        raise RuntimeError("probe_network_forbidden_browser_only")
    class NoNetworkSocket(socket.socket):
        connect = no_network
        connect_ex = no_network
    socket.socket = NoNetworkSocket
    sys.path.insert(0, str(REPO))
    if args.operation == "inject-next":
        result = inject(args, output)
    elif args.operation == "observe":
        if args.index is None:
            parser.error("observe requires --index")
        result = observe(args, output)
    else:
        result = summary(output)
    guard(args)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
