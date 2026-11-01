from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.application.research_intake import (
    LATEST_ADVICE_SOURCE_ID,
    normalize_latest_advice_request,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, ObservationRecord
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    initial_research_state,
    requirements_for_round,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"
NOW = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)


def test_untyped_manual_btc_snapshot_uses_snapshot_policy(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'snapshot.sqlite3'}")
    database.create_all()
    event_id, _envelope, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text=(
                "请对当前 BTC/USDT 做一次市场快照，取得现货价格、成交量、"
                "永续资金费率、未平仓量、基差和订单簿。"
            ),
            source_id="decision-desk",
            source_type=SourceType.manual,
            language="zh",
        )
    )

    assert admitted
    assert database.get_event_type(event_id) == "crypto_market_snapshot"

    from packages.kernel.decision_hub_kernel.application.run import RunService

    run_id, created = RunService(database).create(event_id, strategy_version="research.v1")
    assert created
    request = ResearchRequestFactory(
        database,
        pack_root=PACK_ROOT,
        allowed_capabilities=(
            "official.macro",
            "market.crypto_derivatives",
            "data.historical",
            "web.fetch",
        ),
        clock=lambda: NOW,
    ).build(event_id, run_id)

    assert {item.requirement_id for item in request.evidence_requirements} == {
        "macro_transmission",
        "cross_asset_confirmation",
        "crypto_spot_confirmation",
        "derivatives_crowding",
        "market_regime_context",
        "market_narrative",
    }
    assert {
        item.requirement_id
        for item in request.evidence_requirements
        if item.importance == "hard"
    } == {"crypto_spot_confirmation", "market_regime_context"}
    assert next(
        item for item in request.evidence_requirements
        if item.requirement_id == "derivatives_crowding"
    ).importance == "soft"
    assert "official.macro" not in request.allowed_capabilities
    assert "data.historical" in request.allowed_capabilities


def test_explicit_event_hint_remains_authoritative(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'explicit.sqlite3'}")
    database.create_all()
    event_id, _envelope, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="BTC 价格对央行讲话的反应需要核验。",
            source_id="transcript",
            source_type=SourceType.transcript,
            event_hint="central_bank_speech",
            language="zh",
        )
    )

    assert admitted
    assert database.get_event_type(event_id) == "central_bank_speech"


def test_terse_latest_advice_request_expands_to_fixed_snapshot_research(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'terse.sqlite3'}")
    database.create_all()
    request = normalize_latest_advice_request(
        ObservationCreate(
            text="现在怎么操作",
            source_id="decision-desk",
            source_type=SourceType.manual,
            language="zh",
        )
    )

    assert request.source_id == LATEST_ADVICE_SOURCE_ID
    assert request.event_hint == "crypto_market_snapshot"
    assert "现货价格与成交量" in request.text
    assert "永续资金费率" in request.text
    assert "未来 7 个自然日" in request.text
    assert "不得把未校准的主观概率称为胜率" in request.text

    event_id, _envelope, admitted = AdmissionService(database, clock=lambda: NOW).admit(request)
    assert admitted
    assert database.get_event_type(event_id) == "crypto_market_snapshot"


def test_latest_advice_entry_preserves_optional_user_focus() -> None:
    request = normalize_latest_advice_request(
        ObservationCreate(
            text="重点关注美债收益率和 ETF 资金流",
            source_id=LATEST_ADVICE_SOURCE_ID,
            source_type=SourceType.manual,
            language="zh",
        )
    )

    assert request.event_hint == "crypto_market_snapshot"
    assert request.text.endswith("用户补充关注点：重点关注美债收益率和 ETF 资金流")


def test_non_snapshot_explicit_hint_is_not_rewritten() -> None:
    original = ObservationCreate(
        text="分析这次央行讲话",
        source_id=LATEST_ADVICE_SOURCE_ID,
        source_type=SourceType.manual,
        event_hint="central_bank_speech",
        language="zh",
    )

    assert normalize_latest_advice_request(original) == original


def test_snapshot_request_exposes_narrative_gap_until_search_canary_is_admitted(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'search-enabled.sqlite3'}")
    database.create_all()
    event_id, _envelope, _admitted = AdmissionService(database, clock=lambda: NOW).admit(
        normalize_latest_advice_request(
            ObservationCreate(
                text="获取最新操作建议",
                source_id=LATEST_ADVICE_SOURCE_ID,
                source_type=SourceType.manual,
                language="zh",
            )
        )
    )
    from packages.kernel.decision_hub_kernel.application.run import RunService

    run_id, _created = RunService(database).create(event_id, strategy_version="research.v1")
    request = ResearchRequestFactory(
        database,
        pack_root=PACK_ROOT,
        allowed_capabilities=("web.search", "web.fetch"),
        clock=lambda: NOW,
    ).build(event_id, run_id)

    narrative = next(
        item for item in request.evidence_requirements if item.requirement_id == "market_narrative"
    )
    assert narrative.importance == "soft"
    assert narrative.preferred_capabilities == ["web.search"]
    assert narrative.allowed_fallbacks == ["web.fetch"]

    no_search_run_id, _created = RunService(database).create(
        event_id, strategy_version="research.no-search.v1"
    )
    no_search = ResearchRequestFactory(
        database,
        pack_root=PACK_ROOT,
        allowed_capabilities=("web.fetch", "market.crypto_derivatives"),
        clock=lambda: NOW,
    ).build(event_id, no_search_run_id)
    unavailable = next(
        item
        for item in no_search.evidence_requirements
        if item.requirement_id == "market_narrative"
    )
    assert unavailable.preferred_capabilities == ["web.search"]
    assert unavailable.allowed_fallbacks == []
    assert not (
        set(unavailable.preferred_capabilities) | set(unavailable.allowed_fallbacks)
    ).intersection(no_search.allowed_capabilities)
    projected = requirements_for_round(
        no_search.evidence_requirements,
        initial_research_state(no_search),
        allowed_capabilities=no_search.allowed_capabilities,
    )
    assert "market_narrative" not in {item.requirement_id for item in projected}


def test_latest_advice_uses_a_fresh_snapshot_for_each_new_request_key(
    tmp_path: Path,
) -> None:
    """A repeated "latest" prompt must not silently replay yesterday's event."""

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'latest-idempotency.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, executor=object())  # queue_research does not execute
    request = normalize_latest_advice_request(
        ObservationCreate(
            text="获取最新操作建议",
            source_id=LATEST_ADVICE_SOURCE_ID,
            source_type=SourceType.manual,
            language="zh",
        )
    )

    first_event, first_run, first_created = service.queue_research(request, "latest-001")
    retry_event, retry_run, retry_created = service.queue_research(request, "latest-001")
    second_event, second_run, second_created = service.queue_research(request, "latest-002")

    assert first_created is True
    assert retry_created is False
    assert (retry_event, retry_run) == (first_event, first_run)
    assert second_created is True
    assert second_event != first_event
    assert second_run != first_run


def test_research_api_fixed_entry_normalizes_simple_user_input(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'api-fixed-entry.sqlite3'}")
    client = TestClient(create_app(database))

    response = client.post(
        "/v1/research/observations",
        headers={"Idempotency-Key": "latest-advice-api-001"},
        json={
            "text": "给我最新操作建议",
            "source_id": LATEST_ADVICE_SOURCE_ID,
            "source_type": "manual",
            "language": "zh",
        },
    )

    assert response.status_code == 202
    event_id = response.json()["event_id"]
    assert database.get_event_type(event_id) == "crypto_market_snapshot"
    with database.session() as session:
        stored = session.query(ObservationRecord).filter_by(event_id=event_id).one()
        assert stored.source_id == LATEST_ADVICE_SOURCE_ID
        assert "现货价格与成交量" in stored.text
        assert "未来 7 个自然日" in stored.text
