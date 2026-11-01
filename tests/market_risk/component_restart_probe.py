"""Fresh-process mechanical component rollback probe. No financial qualification."""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from apps.hub_worker.market_component_versions import load_component_versions
from apps.hub_worker.market_components import MarketComponents
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentQualification
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"


class MechanicalReader:
    """Reach pointer/consumer storage only; never real approval evidence."""

    def verify(self, manifest, trial):
        return ComponentQualification(
            trial_id=trial.trial_id,
            manifest_hash=manifest.payload_hash,
            protocol_hash=trial.protocol_hash,
            known_at=trial.end_at,
            original_refs=("fixture://restart-mechanics-only",),
            original_hashes=(canonical_hash({"mechanics_only": True}),),
            status="passed",
            reasons=(),
            sample_qualified=True,
            cost_verified=True,
        )


def baseline(payload):
    raise AssertionError("approved rollback cannot use baseline")


def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    stage = sys.argv[2]
    root = Path(settings["root"])
    os.environ["DECISION_HUB_DATA_DIR"] = str(root / "bootstrap")
    raw = Path(settings["receipt"]).read_bytes()
    installed = []
    for reference in settings["archives"]:
        installed.extend(
            load_component_versions(
                repository_root=root,
                validated_receipt_path=Path(settings["receipt"]),
                validated_receipt_sha256=hashlib.sha256(raw).hexdigest(),
                **reference,
            )
        )
    database = Database("sqlite+pysqlite:///" + str(root / "private.sqlite3"))
    # CURRENT application's checked-in Alembic owns startup in both processes.
    database.initialize()
    now = [NOW - timedelta(days=2)]
    registry = ComponentRegistry(database, original_reader=MechanicalReader(), clock=lambda: now[0])
    consumer = MarketComponents(registry, tuple(installed))
    consumer.register_installed()
    version = "one" if stage == "first" else "two"
    registry.shadow(
        "restart-trial-" + version,
        version_id=version,
        protocol_hash="a" * 64,
        baseline_version_id=None,
        start_at=NOW - timedelta(days=1),
        end_at=NOW - timedelta(hours=1),
    )
    now[0] = NOW
    review = registry.review(
        "restart-review-" + version,
        trial_id="restart-trial-" + version,
        reviewer_ref="fixture://restart-mechanics-only",
    )
    registry.activate(
        "restart-activate-" + version,
        review_id=review.review_id,
        expected_generation=0 if stage == "first" else 1,
    )
    if stage == "second":
        registry.rollback(
            "restart-rollback",
            kind="feature",
            component_ref="restart-fixture",
            scope_id=SCOPE,
            expected_generation=2,
        )
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        output, receipt = consumer(
            session,
            kind="feature",
            component_ref="restart-fixture",
            scope_id=SCOPE,
            payload={"value": 4},
            fallback=baseline,
            request_id="restart-consume-" + stage,
            known_at=NOW,
        )
        head = session.scalar(text("SELECT version_num FROM alembic_version"))
        rows = session.scalar(text("SELECT count(*) FROM market_component_audits"))
    database.engine.dispose()
    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "output": output,
                "receipt": receipt,
                "head": head,
                "audit_count": rows,
                "financial_qualification": False,
            }
        )
    )


if __name__ == "__main__":
    main()
