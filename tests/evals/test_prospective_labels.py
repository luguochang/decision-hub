import json
import math
from datetime import datetime, timedelta

import pytest

from packages.evals.prospective_labels import ProspectiveLabelService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ProspectiveArmRecord,
    ProspectiveLabelAttemptRecord,
    ProspectiveLabelRecord,
    RawCaptureRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import MarketQuote, PriceWindow
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.evals.test_prospective_pairs import add_source
from tests.evals.test_prospective_pairs import context as context
from tests.evals.test_prospective_pairs import pair_context as pair_context


class ArchivedMarket:
    """Controlled test archive; never used as live evidence."""

    def __init__(self, c):
        self.c, self.calls = c, 0
        self.bad_hash, self.failure = False, False

    async def window(self, instrument, start, end):
        self.calls += 1
        if self.failure:
            raise RuntimeError("provider failure containing a private detail")
        c = self.c
        quotes = []
        with c.database.session() as session:
            attempt = session.get(ProspectiveLabelRecord, c.pair_id).attempt
            for side, boundary, price in [("entry", start, 100), ("exit", end, 102)]:
                stamp = datetime.fromtimestamp(
                    math.ceil(boundary.timestamp() / 60) * 60, boundary.tzinfo
                )
                body = json.dumps(
                    {
                        "code": "0",
                        "data": [
                            [
                                str(int((stamp - timedelta(minutes=1)).timestamp() * 1000)),
                                "100",
                                "102",
                                "99",
                                str(price),
                                "1",
                                "1",
                                "1",
                                "1",
                            ]
                        ],
                    }
                ).encode()
                digest, ref = RawBodyArchive(c.tmp_path / "raw-captures").put(body)
                session.add(
                    RawCaptureRecord(
                        capture_id=f"capture-{attempt}-{side}",
                        context_id=f"analytical-observation:paired:{c.pair_id}:{attempt}",
                        url=(
                            "https://www.okx.com/api/v5/market/history-candles?instId="
                            f"{instrument}&bar=1m&after={int(boundary.timestamp() * 1000)}&limit=1"
                        ),
                        status_code=200,
                        body_hash="wrong" if self.bad_hash else digest,
                        body_ref=ref,
                        byte_count=len(body),
                        media_type="application/json",
                        adapter_version="test",
                        started_at=c.clock[0],
                        completed_at=c.clock[0],
                        recorded_at=c.clock[0],
                    )
                )
                quotes.append(
                    MarketQuote(
                        instrument=instrument,
                        observed_at=stamp,
                        received_at=c.clock[0],
                        last=price,
                        source_id="okx-public",
                        quality_status="estimated",
                        benchmark="close_1m",
                    )
                )
        return PriceWindow(entry=quotes[0], exit=quotes[1], quality_status="estimated")


@pytest.fixture
async def labelled(pair_context):
    c = pair_context
    add_source(c)
    run = await c.worker.tick()
    c.pair_id = run["pair_id"]
    c.view = c.pairs.get(c.pair_id)
    c.market = ArchivedMarket(c)
    c.labels = ProspectiveLabelService(
        c.database, c.pairs, c.market, c.tmp_path, clock=lambda: c.clock[0]
    )
    c.due = datetime.fromisoformat(c.view["window_end_at"]) + timedelta(minutes=1)
    return c


async def test_future_labels_are_pending_and_reading_never_fetches(labelled):
    c = labelled
    assert await c.labels.process_due(c.trial.trial_id) == 0
    assert c.market.calls == 0
    view = c.labels.get(c.pair_id)
    assert view["status"] == "pending" and view["result"] is None
    assert c.market.calls == 0


async def test_both_methods_use_one_archived_label_and_keep_original_results(labelled):
    c = labelled
    c.clock[0] = c.due
    before = c.pairs.get(c.pair_id)
    assert await c.labels.process_due(c.trial.trial_id) == 1
    label = c.labels.get(c.pair_id)
    assert label["status"] == "completed" and label["attempt"] == 1
    result = label["result"]
    assert result["market_return_pct"] == pytest.approx(2)
    assert result["paired_correctness_delta"] == 0
    assert result["scores"]["baseline"]["direction_correct"] is True
    assert result["scores"]["candidate"]["direction_correct"] is True
    assert len(result["captures"]) == 2 and not result["promotion_eligible"]
    assert result["quality_status"] == "estimated"
    after = c.pairs.get(c.pair_id)
    assert after["label_status"] == "completed"
    assert {k: v for k, v in after.items() if k != "label_status"} == {
        k: v for k, v in before.items() if k != "label_status"
    }
    assert await c.labels.process_due(c.trial.trial_id) == 0
    assert c.market.calls == 1 and c.labels.get(c.pair_id) == label


async def test_hash_failure_is_recorded_then_retry_retains_failed_attempt(labelled):
    c = labelled
    c.clock[0], c.market.bad_hash = c.due, True
    assert await c.labels.process_due(c.trial.trial_id) == 0
    label = c.labels.get(c.pair_id)
    assert label["status"] == "retry" and label["error_code"] == "archive_hash_mismatch"
    assert label["result"] is None
    c.market.bad_hash = False
    c.clock[0] += timedelta(minutes=2)
    assert await c.labels.process_due(c.trial.trial_id) == 1
    with c.database.session() as session:
        attempts = (
            session.query(ProspectiveLabelAttemptRecord)
            .order_by(
                ProspectiveLabelAttemptRecord.attempt,
            )
            .all()
        )
        assert [a.status for a in attempts] == ["failed", "completed"]
    assert c.labels.get(c.pair_id)["attempt"] == 2


async def test_collection_error_redacted_and_lease_recovery_fenced(labelled):
    c = labelled
    c.clock[0] = c.due
    c.labels.register_missing(c.trial.trial_id)
    claim = c.labels.claim(c.pair_id)
    assert claim and c.labels.claim(c.pair_id) is None
    c.clock[0] += timedelta(seconds=91)
    c.labels.recover_expired()
    assert not c.labels.finish(c.pair_id, *claim, result={"stale": True})
    c.market.failure = True
    assert await c.labels.process_due(c.trial.trial_id) == 0
    view = c.labels.get(c.pair_id)
    assert "private" not in json.dumps(view)
    assert view["error_code"] == "pair_label_collection_failed"
    with c.database.session() as session:
        assert session.get(ProspectiveLabelAttemptRecord, (c.pair_id, 1)).status == "interrupted"


@pytest.mark.parametrize("direction", ["neutral", "no_trade"])
async def test_neutral_and_abstention_are_not_hits_or_zero_profit(labelled, direction):
    from packages.runtime_adapters.execution_recipe import canonical_recipe, recipe_hash

    c = labelled
    # An isolated frozen response variation; never updates a live result.
    with c.database.session() as session:
        row = session.get(ProspectiveArmRecord, (c.pair_id, "candidate"))
        result = json.loads(row.result_json)
        result["payload"]["direction"] = direction
        row.result_json, row.result_hash = canonical_recipe(result), recipe_hash(result)
    c.clock[0] = c.due
    assert await c.labels.process_due(c.trial.trial_id) == 1
    result = c.labels.get(c.pair_id)["result"]
    assert result["scores"]["candidate"]["direction_correct"] is None
    assert result["scores"]["candidate"]["directional_return_pct"] is None
    assert result["paired_correctness_delta"] is None


async def test_incomplete_prediction_is_retained_in_common_label(labelled):
    c = labelled
    with c.database.session() as session:
        row = session.get(ProspectiveArmRecord, (c.pair_id, "candidate"))
        row.status, row.result_json, row.result_hash = "unknown", None, None
    c.clock[0] = c.due
    assert await c.labels.process_due(c.trial.trial_id) == 1
    result = c.labels.get(c.pair_id)["result"]
    assert result["scores"]["candidate"]["status"] == "unknown"
    assert result["scores"]["candidate"]["direction_correct"] is None
    assert result["scores"]["baseline"]["direction_correct"] is True
    assert result["paired_correctness_delta"] is None


async def test_saved_label_corruption_is_detected(labelled):
    c = labelled
    c.clock[0] = c.due
    assert await c.labels.process_due(c.trial.trial_id) == 1
    with c.database.session() as session:
        session.get(ProspectiveLabelRecord, c.pair_id).result_json = "{}"
    with pytest.raises(ValueError, match="pair_label_corrupt"):
        c.labels.get(c.pair_id)


@pytest.mark.parametrize("changed", ["input", "trial", "arm"])
async def test_label_cannot_be_rebound_with_a_new_hash(labelled, changed):
    from packages.runtime_adapters.execution_recipe import canonical_recipe, recipe_hash

    c = labelled
    c.clock[0] = c.due
    assert await c.labels.process_due(c.trial.trial_id) == 1
    with c.database.session() as session:
        row = session.get(ProspectiveLabelRecord, c.pair_id)
        result = json.loads(row.result_json)
        if changed == "input":
            result["input_hash"] = "another-input"
        elif changed == "trial":
            result["trial_definition_hash"] = "another-definition"
        else:
            result["scores"]["candidate"]["result_hash"] = "another-prediction"
        row.result_json, row.result_hash = canonical_recipe(result), recipe_hash(result)
    with pytest.raises(ValueError, match="pair_label_binding_mismatch"):
        c.labels.get(c.pair_id)
    assert c.market.calls == 1


async def test_pc_receipt_api_paginates_and_does_not_fetch_or_expose_recipe(labelled):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.hub_api.asset_routes import asset_router

    c = labelled
    c.clock[0] = c.due
    await c.labels.process_due(c.trial.trial_id)
    app = FastAPI()
    app.include_router(asset_router(c.database, c.tmp_path, clock=lambda: c.clock[0]))
    client = TestClient(app)
    trials = client.get("/v1/evolution/prospective-trials")
    assert trials.status_code == 200 and len(trials.json()) == 1
    assert "system_prompt" not in trials.text and "endpoint_hash" not in trials.text
    page = client.get("/v1/evolution/prospective-pairs",
                      params={"trial_id": c.trial.trial_id, "limit": 1}).json()
    assert not page["has_more"] and page["items"][0]["label"]["status"] == "completed"
    empty = client.get("/v1/evolution/prospective-pairs",
                       params={"trial_id": c.trial.trial_id, "offset": 1}).json()
    assert empty["items"] == []
    detail = client.get(f"/v1/evolution/prospective-pairs/{c.pair_id}")
    assert detail.status_code == 200
    assert detail.json()["pair"]["label_status"] == "completed"
    assert c.market.calls == 1 and len(c.calls) == 2
