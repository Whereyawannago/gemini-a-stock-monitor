"""Tests for gold price monitoring, premium calculation, alerts, cooldown, and cards."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_monitor.config import Settings, parse_price_levels
from stock_monitor.feishu_notifier import build_gold_card_payload
from stock_monitor.gold_monitor import (
    GoldAlertEngine,
    build_rule_gold_summary,
    compute_sge_30m_change_pct,
    is_gold_market_open,
    parse_foreign_gold_quotes,
    parse_usd_cny,
)
from stock_monitor.main import main
from stock_monitor.mock_data import build_mock_gold_snapshot
from stock_monitor.models import AnalysisResult, GoldAlertType, PriceLevel
from stock_monitor.pipeline import MonitorPipeline


def test_is_gold_market_open_weekend_window() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    # Friday 22:00 -> open
    assert is_gold_market_open(datetime(2026, 11, 27, 22, 0, tzinfo=tz)) is True
    # Saturday 03:00 -> still open (US Friday afternoon)
    assert is_gold_market_open(datetime(2026, 11, 28, 3, 0, tzinfo=tz)) is True
    # Saturday 10:00 -> closed
    assert is_gold_market_open(datetime(2026, 11, 28, 10, 0, tzinfo=tz)) is False
    # Sunday 15:00 -> closed
    assert is_gold_market_open(datetime(2026, 11, 29, 15, 0, tzinfo=tz)) is False
    # Monday 05:00 -> closed
    assert is_gold_market_open(datetime(2026, 11, 30, 5, 0, tzinfo=tz)) is False
    # Monday 07:00 -> open
    assert is_gold_market_open(datetime(2026, 11, 30, 7, 0, tzinfo=tz)) is True


def test_parse_usd_cny_and_foreign_gold() -> None:
    fx_df = pd.DataFrame([{"货币对": "USD/CNY", "买报价": 7.00, "卖报价": 7.02}])
    rate = parse_usd_cny(fx_df)
    assert rate == pytest.approx(7.01)

    foreign_df = pd.DataFrame(
        [
            {
                "名称": "COMEX黄金",
                "最新价": 3600.0,
                "人民币报价": 0.0,
                "涨跌幅": 1.25,
                "昨日结算价": 3555.56,
                "最高价": 3610.0,
                "最低价": 3550.0,
                "日期": "2026-11-26",
                "行情时间": "21:00:00",
            },
            {
                "名称": "伦敦金",
                "最新价": 3580.0,
                "人民币报价": 806.85,
                "涨跌幅": -0.35,
                "昨日结算价": 3592.5,
                "最高价": 3600.0,
                "最低价": 3560.0,
                "日期": "2026-11-26",
                "行情时间": "21:00:00",
            },
        ]
    )
    quotes = parse_foreign_gold_quotes(foreign_df, rate)
    assert len(quotes) == 2
    assert quotes[0].key == "COMEX"
    assert quotes[0].cny_per_gram == pytest.approx(3600.0 * 7.01 / 31.1034768, abs=0.05)
    assert quotes[1].key == "LONDON"
    assert quotes[1].cny_per_gram == pytest.approx(806.85)


def test_compute_sge_30m_change_pct() -> None:
    # 35 one-minute rows: index -31 is 760.0, last is 767.6 -> +1.0%
    prices = [760.0] * 34 + [767.6]
    df = pd.DataFrame({"现价": prices})
    assert compute_sge_30m_change_pct(df) == pytest.approx(1.0, abs=0.01)


def test_parse_price_levels_and_trigger_with_cooldown(tmp_path) -> None:
    levels = parse_price_levels("AU9999:below:750,COMEX:above:3650,invalid:foo:1")
    assert levels == (
        PriceLevel(key="AU9999", direction="below", price=750.0),
        PriceLevel(key="COMEX", direction="above", price=3650.0),
    )

    tz = ZoneInfo("Asia/Shanghai")
    t0 = datetime(2026, 11, 26, 10, 0, tzinfo=tz)
    settings = Settings(
        state_dir=tmp_path,
        gold_alert_30m_pct=1.0,
        gold_alert_daily_pct=2.0,
        gold_cooldown_minutes=60,
        gold_price_levels=levels,
    )
    engine = GoldAlertEngine(settings)

    # Plunge scenario: Au99.99=744.80 (below 750 + 30m -1.25% + daily -2.30%)
    snap1 = build_mock_gold_snapshot("plunge", now=t0)
    active1, suppressed1 = engine.evaluate(snap1, persist=True)
    assert any(a.alert_type == GoldAlertType.LEVEL_BELOW and a.key == "AU9999" for a in active1)
    assert any(a.alert_type == GoldAlertType.FAST_DOWN for a in active1)
    assert len(suppressed1) == 0

    # 30 minutes later: same alerts must be suppressed by 60-min cooldown
    snap2 = build_mock_gold_snapshot("plunge", now=t0 + timedelta(minutes=30))
    active2, suppressed2 = engine.evaluate(snap2, persist=True)
    assert len(active2) == 0
    assert len(suppressed2) == len(active1)

    # 60 minutes later: cooldown expired, fires again
    snap3 = build_mock_gold_snapshot("plunge", now=t0 + timedelta(minutes=60))
    active3, _ = engine.evaluate(snap3, persist=True)
    assert len(active3) == len(active1)


def test_gold_rolling_history_detects_30m_move(tmp_path) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    t0 = datetime(2026, 11, 26, 14, 0, tzinfo=tz)
    settings = Settings(state_dir=tmp_path, gold_alert_30m_pct=1.0, gold_alert_daily_pct=5.0)
    engine = GoldAlertEngine(settings)

    # First poll records baseline price (no built-in change_30m_pct)
    snap0 = build_mock_gold_snapshot("normal", now=t0)
    for q in snap0.quotes:
        q.change_30m_pct = None
    active0, _ = engine.evaluate(snap0, persist=True)
    assert active0 == []

    # 30m later, COMEX jumps +1.5%
    snap1 = build_mock_gold_snapshot("normal", now=t0 + timedelta(minutes=30))
    for q in snap1.quotes:
        q.change_30m_pct = None
        if q.key == "COMEX":
            q.price = round(q.price * 1.015, 2)
    active1, _ = engine.evaluate(snap1, persist=True)
    assert any(a.key == "COMEX" and a.alert_type == GoldAlertType.FAST_UP for a in active1)


def test_gold_poll_skips_when_normal_and_sends_when_alert(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path, minimax_api_key="")
    pipeline = MonitorPipeline(settings)

    # Normal poll -> no alert -> not sent
    res_normal = pipeline.run_gold_check(mode="poll", mock=True, mock_scenario="normal", dry_run=True)
    assert res_normal.sent is False

    # Normal brief -> always sent (yellow card)
    res_brief = pipeline.run_gold_check(mode="brief", mock=True, mock_scenario="normal", dry_run=True)
    assert res_brief.sent is True
    assert res_brief.delivery["payload"]["card"]["header"]["template"] == "yellow"

    # Surge poll -> alert triggered -> sent (red card)
    res_surge = pipeline.run_gold_check(mode="poll", mock=True, mock_scenario="surge", dry_run=True)
    assert res_surge.sent is True
    assert res_surge.delivery["payload"]["card"]["header"]["template"] == "red"

    # Plunge card -> green
    snap = build_mock_gold_snapshot("plunge")
    alerts, _ = GoldAlertEngine(settings).evaluate(snap, persist=False)
    card = build_gold_card_payload(
        snap,
        alerts,
        AnalysisResult(content=build_rule_gold_summary(snap, alerts), source="rule_fallback"),
    )
    assert card["card"]["header"]["template"] == "green"


def test_cli_gold_mock_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "")

    assert main(["--once", "--job", "gold", "--mock", "--dry-run", "--print-json"]) == 0
    out = capsys.readouterr().out
    assert '"template": "yellow"' in out
    assert "Au99.99" in out
    assert "COMEX" in out
