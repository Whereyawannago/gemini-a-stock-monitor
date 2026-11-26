"""Tests for quantitative alert classification and 60-minute cooldown."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_monitor.alert_engine import AlertEngine
from stock_monitor.config import Settings
from stock_monitor.mock_data import build_mock_snapshot
from stock_monitor.models import AlertLevel


def _make_settings(tmp_path: Path, cooldown_minutes: int = 60) -> Settings:
    return Settings(
        state_dir=tmp_path,
        alert_cooldown_minutes=cooldown_minutes,
    )


def test_normal_market_returns_normal_level(tmp_path: Path) -> None:
    engine = AlertEngine(_make_settings(tmp_path))
    snap = build_mock_snapshot("normal")
    result = engine.evaluate(snap)
    assert result.raw_level == AlertLevel.NORMAL
    assert result.effective_level == AlertLevel.NORMAL
    assert not result.is_suppressed_by_cooldown


def test_volume_surge_and_60min_cooldown_suppression(tmp_path: Path) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    t0 = datetime(2026, 11, 25, 10, 0, tzinfo=tz)
    engine = AlertEngine(_make_settings(tmp_path, cooldown_minutes=60))

    # First surge at 10:00 -> triggers red alert
    res1 = engine.evaluate(build_mock_snapshot("surge", now=t0), persist=True)
    assert res1.raw_level == AlertLevel.VOLUME_SURGE
    assert res1.effective_level == AlertLevel.VOLUME_SURGE
    assert not res1.is_suppressed_by_cooldown

    # Second surge at 10:30 (30 min later) -> suppressed by 60-min cooldown
    res2 = engine.evaluate(
        build_mock_snapshot("surge", now=t0 + timedelta(minutes=30)), persist=True
    )
    assert res2.raw_level == AlertLevel.VOLUME_SURGE
    assert res2.effective_level == AlertLevel.NORMAL
    assert res2.is_suppressed_by_cooldown
    assert res2.cooldown_remaining_minutes == 30

    # Third surge at 11:00 (60 min later) -> cooldown expired, fires again
    res3 = engine.evaluate(
        build_mock_snapshot("surge", now=t0 + timedelta(minutes=60)), persist=True
    )
    assert res3.raw_level == AlertLevel.VOLUME_SURGE
    assert res3.effective_level == AlertLevel.VOLUME_SURGE
    assert not res3.is_suppressed_by_cooldown


def test_trend_reversal_bypasses_cooldown(tmp_path: Path) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    t0 = datetime(2026, 11, 25, 10, 0, tzinfo=tz)
    engine = AlertEngine(_make_settings(tmp_path, cooldown_minutes=60))

    res1 = engine.evaluate(build_mock_snapshot("surge", now=t0), persist=True)
    assert res1.effective_level == AlertLevel.VOLUME_SURGE

    # Reversal to volume_plunge 30 min later -> must fire immediately
    res2 = engine.evaluate(
        build_mock_snapshot("plunge", now=t0 + timedelta(minutes=30)), persist=True
    )
    assert res2.raw_level == AlertLevel.VOLUME_PLUNGE
    assert res2.effective_level == AlertLevel.VOLUME_PLUNGE
    assert not res2.is_suppressed_by_cooldown


def test_dry_run_does_not_persist_state(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    engine = AlertEngine(settings)
    snap = build_mock_snapshot("surge")
    res = engine.evaluate(snap, persist=False)
    assert res.effective_level == AlertLevel.VOLUME_SURGE
    assert not settings.alert_state_file.exists()
