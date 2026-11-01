from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.provider_adapters.historical import (
    BinanceVisionEventWindowLoader,
    KalshiHistoricalExpectationLoader,
    PortaraCrossAssetEventWindowLoader,
    PortaraMacroEventWindowLoader,
)


def _zip(rows: list[list[object]], *, header: list[str] | None = None) -> bytes:
    raw = io.StringIO(newline="")
    writer = csv.writer(raw, lineterminator="\n")
    if header is not None:
        writer.writerow(header)
    writer.writerows(rows)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("source.csv", raw.getvalue())
    return output.getvalue()


@pytest.mark.asyncio
async def test_binance_vision_loader_preserves_event_offsets_and_units() -> None:
    event = datetime(2025, 1, 15, 13, 30, tzinfo=UTC)

    def ms(value: datetime, *, microseconds: bool = False) -> int:
        result = int(value.timestamp() * 1000)
        return result * 1000 if microseconds else result

    spot = _zip(
        [
            [
                ms(datetime(2025, 1, 15, 13, 24, tzinfo=UTC), microseconds=True),
                1,
                1,
                1,
                90,
                2,
                ms(datetime(2025, 1, 15, 13, 25, tzinfo=UTC), microseconds=True),
                180,
                1,
                1,
                90,
                0,
            ],
            [
                ms(datetime(2025, 1, 15, 13, 30, tzinfo=UTC), microseconds=True),
                1,
                1,
                1,
                99,
                3,
                ms(datetime(2025, 1, 15, 13, 31, tzinfo=UTC), microseconds=True),
                297,
                1,
                1,
                99,
                0,
            ],
        ]
    )
    kline_header = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]
    premium = _zip(
        [
            [
                ms(datetime(2025, 1, 15, 13, 24, tzinfo=UTC)),
                0,
                0,
                0,
                "0.001",
                0,
                ms(datetime(2025, 1, 15, 13, 25, tzinfo=UTC)),
                0,
                1,
                0,
                0,
                0,
            ],
            [
                ms(datetime(2025, 1, 15, 13, 30, tzinfo=UTC)),
                0,
                0,
                0,
                "0.002",
                0,
                ms(datetime(2025, 1, 15, 13, 31, tzinfo=UTC)),
                0,
                1,
                0,
                0,
                0,
            ],
        ],
        header=kline_header,
    )
    metrics = _zip(
        [
            ["2025-01-15 13:25:00", "BTCUSDT", 10, 1000, "1.1"],
            ["2025-01-15 13:30:00", "BTCUSDT", 11, 1100, "1.2"],
        ],
        header=[
            "create_time",
            "symbol",
            "sum_open_interest",
            "sum_open_interest_value",
            "count_long_short_ratio",
        ],
    )
    funding = _zip(
        [[ms(datetime(2025, 1, 15, 8, tzinfo=UTC)), 8, "0.0001"]],
        header=["calc_time", "funding_interval_hours", "last_funding_rate"],
    )

    async def fetcher(url: str) -> bytes:
        if "/spot/" in url:
            return spot
        if "/metrics/" in url:
            return metrics
        if "/premiumIndexKlines/" in url:
            return premium
        return funding

    window = await BinanceVisionEventWindowLoader(fetcher=fetcher).load(event)

    assert [item.event_offset for item in window.points] == ["t-5m", "t+1m"]
    assert window.points[0].values == {
        "price": "90",
        "volume": "180",
        "funding_rate": "0.0001",
        "open_interest": "1000",
        "basis": "0.001",
        "crowding_signal": "1.1",
    }
    assert window.points[1].values["open_interest"] == "1100"
    assert len({item.content_hash for item in window.sources}) == 4


@pytest.mark.asyncio
async def test_kalshi_loader_selects_liquid_market_and_real_timestamps() -> None:
    event = datetime(2024, 9, 18, 18, tzinfo=UTC)

    async def fetcher(url: str):
        if "/historical/markets?" in url:
            return {
                "markets": [
                    {"ticker": "LOW", "title": "Low volume"},
                    {"ticker": "HIGH", "title": "High volume"},
                ]
            }
        volume = "100" if "/HIGH/" in url else "1"
        return {
            "candlesticks": [
                {
                    "end_period_ts": int(datetime(2024, 9, 17, 17, 55, tzinfo=UTC).timestamp()),
                    "price": {"close": "0.40", "previous": "0.39"},
                    "volume": volume,
                },
                {
                    "end_period_ts": int(datetime(2024, 9, 18, 17, 55, tzinfo=UTC).timestamp()),
                    "price": {"close": None, "previous": "0.60"},
                    "volume": volume,
                },
            ]
        }

    window = await KalshiHistoricalExpectationLoader(fetcher=fetcher).load(
        event_ticker="FED-24SEP", event_at=event
    )

    assert window.market_ticker == "HIGH"
    assert [(item.event_offset, item.probability) for item in window.points] == [
        ("baseline", "0.40"),
        ("t-5m", "0.60"),
    ]
    assert window.points[1].observed_at == datetime(2024, 9, 18, 17, 55, tzinfo=UTC)


@pytest.mark.asyncio
async def test_kalshi_loader_rejects_a_stale_bar_labeled_as_t5() -> None:
    event = datetime(2024, 11, 7, 19, tzinfo=UTC)

    async def fetcher(url: str):
        if "/historical/markets?" in url:
            return {"markets": [{"ticker": "FED", "title": "Fed outcome"}]}
        return {
            "candlesticks": [
                {
                    "end_period_ts": int(datetime(2024, 11, 6, 18, 55, tzinfo=UTC).timestamp()),
                    "price": {"close": "0.40", "previous": "0.39"},
                    "volume": "10",
                },
                {
                    "end_period_ts": int(datetime(2024, 11, 7, 18, 46, tzinfo=UTC).timestamp()),
                    "price": {"close": "0.60", "previous": "0.59"},
                    "volume": "10",
                },
            ]
        }

    with pytest.raises(ValueError, match="kalshi_expectation_window_missing"):
        await KalshiHistoricalExpectationLoader(fetcher=fetcher).load(
            event_ticker="FED-24NOV", event_at=event
        )


def _portara_file(path: Path, rows: list[tuple[str, str, str]]) -> None:
    path.write_text(
        "Date,Time,Open,High,Low,Close,Volume\n"
        + "".join(
            f"{date},{time},{close},{close},{close},{close},1\n" for date, time, close in rows
        ),
        encoding="utf-8",
    )


def test_portara_loader_builds_two_origin_rates_usd_window(tmp_path: Path) -> None:
    rate_path = tmp_path / "USY02Y.txt"
    usd_path = tmp_path / "DXA.txt"
    _portara_file(
        rate_path,
        [
            ("20240917", "1300", "4.100"),
            ("20240918", "1255", "4.000"),
            ("20240918", "1301", "3.950"),
        ],
    )
    _portara_file(
        usd_path,
        [("20240918", "1255", "100.000"), ("20240918", "1301", "99.800")],
    )

    window = PortaraMacroEventWindowLoader(
        us2y_path=rate_path,
        dxy_path=usd_path,
    ).load(datetime(2024, 9, 18, 18, tzinfo=UTC))

    rates, usd = window.series
    assert (rates.metric_family, rates.instrument, rates.venue) == (
        "macro.rates",
        "US2Y",
        "tullett-prebon",
    )
    assert [(item.event_offset, item.level, item.event_return) for item in rates.points] == [
        ("t-5m", "4", "0"),
        ("t+1m", "3.95", "-5"),
    ]
    assert [(item.event_offset, item.level, item.event_return) for item in usd.points] == [
        ("t-5m", "100", "0"),
        ("t+1m", "99.8", "-0.2"),
    ]
    assert rates.source_id != usd.source_id
    assert rates.source_hash != usd.source_hash

    coverage = PortaraMacroEventWindowLoader(
        us2y_path=rate_path,
        dxy_path=usd_path,
    ).coverage(datetime(2024, 9, 18, 18, tzinfo=UTC))
    series = {item["instrument"]: item for item in coverage["series"]}
    assert all(slot["status"] == "available" for item in series.values() for slot in item["slots"])
    assert coverage["expectation_pricing"]["slots"][0]["event_offset"] == "baseline"

    expectation = PortaraMacroEventWindowLoader(
        us2y_path=rate_path,
        dxy_path=usd_path,
    ).load_rate_expectation(datetime(2024, 9, 18, 18, tzinfo=UTC))
    assert [(item.event_offset, item.level, item.delta) for item in expectation.points] == [
        ("baseline", "4.1", "0"),
        ("t-5m", "4", "-10"),
    ]


def test_portara_loader_rejects_missing_exact_event_window(tmp_path: Path) -> None:
    rate_path = tmp_path / "USY02Y.txt"
    usd_path = tmp_path / "DXA.txt"
    rows = [("20240918", "1255", "4.000"), ("20240918", "1259", "3.990")]
    _portara_file(rate_path, rows)
    _portara_file(usd_path, rows)

    with pytest.raises(ValueError, match="portara_event_window_stale"):
        PortaraMacroEventWindowLoader(
            us2y_path=rate_path,
            dxy_path=usd_path,
        ).load(datetime(2024, 9, 18, 18, tzinfo=UTC))

    coverage = PortaraMacroEventWindowLoader(
        us2y_path=rate_path,
        dxy_path=usd_path,
    ).coverage(datetime(2024, 9, 18, 18, tzinfo=UTC))
    assert {
        (item["instrument"], slot["event_offset"], slot["reason"])
        for item in coverage["series"]
        for slot in item["slots"]
        if slot["status"] == "gap"
    } == {
        ("US2Y", "t+1m", "portara_event_window_stale"),
        ("DXY", "t+1m", "portara_event_window_stale"),
    }


def test_portara_es_loader_builds_independent_cross_asset_window(tmp_path: Path) -> None:
    es_path = tmp_path / "EP.txt"
    _portara_file(
        es_path,
        [("20240918", "1255", "5000.00"), ("20240918", "1301", "4975.00")],
    )

    window = PortaraCrossAssetEventWindowLoader(es_path=es_path).load(
        datetime(2024, 9, 18, 18, tzinfo=UTC)
    )

    assert (window.metric_family, window.instrument, window.venue) == (
        "cross_asset.equity",
        "ES",
        "cme",
    )
    assert [(item.event_offset, item.level, item.event_return) for item in window.points] == [
        ("t-5m", "5000", "0"),
        ("t+1m", "4975", "-0.5"),
    ]
    assert len(window.source_hash) == 64

    coverage = PortaraCrossAssetEventWindowLoader(es_path=es_path).coverage(
        datetime(2024, 9, 18, 18, tzinfo=UTC)
    )
    assert coverage["instrument"] == "ES"
    assert all(slot["status"] == "available" for slot in coverage["slots"])
