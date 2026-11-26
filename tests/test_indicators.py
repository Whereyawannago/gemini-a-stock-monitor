"""Tests for 5-minute K-line VWAP, YoY volume ratio, and 30m marginal volume."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_monitor.data_fetcher import (
    compute_intraday_vwap,
    compute_marginal_30m_metrics,
    compute_yoy_volume_ratio,
    normalize_kline_df,
    parse_chinese_amount_to_yi,
    split_latest_two_trading_days,
)


def _make_day_bars(day_str: str, bars: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Create normalized 5m K-line bars: (HH:MM, high, low, close, amount)."""
    rows = []
    for hhmm, high, low, close, amount in bars:
        rows.append(
            {
                "datetime": pd.Timestamp(f"{day_str} {hhmm}:00"),
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": amount / 100.0,
                "amount": amount,
            }
        )
    return pd.DataFrame(rows)


def test_compute_intraday_vwap_amount_weighted() -> None:
    df = _make_day_bars(
        "2026-11-25",
        [
            ("09:35", 3310.0, 3290.0, 3300.0, 100e8),  # typical = 3300
            ("09:40", 3370.0, 3350.0, 3360.0, 300e8),  # typical = 3360
        ],
    )
    # Weighted avg = (3300*100 + 3360*300) / 400 = 3345.0
    vwap = compute_intraday_vwap(df)
    assert vwap == pytest.approx(3345.0, abs=0.01)


def test_compute_yoy_volume_ratio_same_time_cutoff() -> None:
    prev_df = _make_day_bars(
        "2026-11-24",
        [
            ("09:35", 3300, 3290, 3295, 100e8),
            ("09:40", 3305, 3295, 3300, 100e8),
            ("09:45", 3310, 3300, 3305, 500e8),  # after today's cutoff, must be excluded
        ],
    )
    today_df = _make_day_bars(
        "2026-11-25",
        [
            ("09:35", 3320, 3310, 3315, 150e8),
            ("09:40", 3330, 3320, 3325, 150e8),
        ],
    )
    ratio, diff_pct = compute_yoy_volume_ratio(today_df, prev_df)
    # Today cum = 300e8, Prev cum up to 09:40 = 200e8 -> 1.5x (+50%)
    assert ratio == pytest.approx(1.5, abs=1e-4)
    assert diff_pct == pytest.approx(50.0, abs=0.01)


def test_compute_marginal_30m_vs_prev_day_same_window() -> None:
    times = ["09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10"]
    prev_bars = [(t, 3300, 3290, 3295, 100e8) for t in times]
    today_bars = [
        ("09:35", 3300, 3290, 3295, 100e8),
        ("09:40", 3300, 3290, 3295, 100e8),
        # Last 6 bars (30 minutes) each 160e8 -> total 960e8 vs prev 600e8 -> 1.6x
        ("09:45", 3310, 3300, 3305, 160e8),
        ("09:50", 3315, 3305, 3310, 160e8),
        ("09:55", 3320, 3310, 3315, 160e8),
        ("10:00", 3325, 3315, 3320, 160e8),
        ("10:05", 3330, 3320, 3325, 160e8),
        ("10:10", 3335, 3325, 3330, 160e8),
    ]
    prev_df = _make_day_bars("2026-11-24", prev_bars)
    today_df = _make_day_bars("2026-11-25", today_bars)

    amount, ratio, diff_pct = compute_marginal_30m_metrics(today_df, prev_df)
    assert amount == pytest.approx(960e8, rel=1e-4)
    assert ratio == pytest.approx(1.6, abs=1e-4)
    assert diff_pct == pytest.approx(60.0, abs=0.01)


def test_compute_marginal_30m_fallback_to_earlier_today() -> None:
    today_bars = [
        ("09:35", 3300, 3290, 3295, 50e8),
        ("09:40", 3300, 3290, 3295, 50e8),
        ("09:45", 3310, 3300, 3305, 100e8),
        ("09:50", 3315, 3305, 3310, 100e8),
        ("09:55", 3320, 3310, 3315, 100e8),
        ("10:00", 3325, 3315, 3320, 100e8),
        ("10:05", 3330, 3320, 3325, 100e8),
        ("10:10", 3335, 3325, 3330, 100e8),
    ]
    today_df = _make_day_bars("2026-11-25", today_bars)
    amount, ratio, diff_pct = compute_marginal_30m_metrics(today_df, prev_df=None)
    # Recent 6 bars = 600e8; earlier avg per bar = 50e8 -> baseline 300e8 -> 2.0x (+100%)
    assert amount == pytest.approx(600e8, rel=1e-4)
    assert ratio == pytest.approx(2.0, abs=1e-4)
    assert diff_pct == pytest.approx(100.0, abs=0.01)


def test_normalize_and_split_two_trading_days() -> None:
    raw = pd.DataFrame(
        [
            {"时间": "2026-11-24 14:55:00", "开盘": 3300, "最高": 3305, "最低": 3298, "收盘": 3302, "成交量": 1000, "成交额": 1e9},
            {"时间": "2026-11-25 09:35:00", "开盘": 3310, "最高": 3320, "最低": 3308, "收盘": 3318, "成交量": 1200, "成交额": 1.2e9},
        ]
    )
    norm = normalize_kline_df(raw)
    today_df, prev_df = split_latest_two_trading_days(norm)
    assert len(today_df) == 1
    assert len(prev_df) == 1
    assert today_df["close"].iloc[0] == 3318.0
    assert prev_df["close"].iloc[0] == 3302.0


def test_parse_chinese_amount_to_yi() -> None:
    assert parse_chinese_amount_to_yi(6842000000) == pytest.approx(68.42, abs=1e-3)
    assert parse_chinese_amount_to_yi("-14.20亿") == pytest.approx(-14.20, abs=1e-3)
    assert parse_chinese_amount_to_yi("5000万") == pytest.approx(0.5, abs=1e-3)
    assert parse_chinese_amount_to_yi("--") == 0.0
