"""Tests for trading calendar and APScheduler 10:00~15:00 job configuration."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_monitor.calendar_utils import TradingCalendar
from stock_monitor.config import DEFAULT_SCHEDULE_TIMES, Settings
from stock_monitor.pipeline import MonitorPipeline
from stock_monitor.scheduler import build_scheduler


def test_trading_calendar_uses_cache_and_excludes_weekends_and_holidays(
    tmp_path: Path,
) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    cache_file = tmp_path / "trade_calendar_cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(tz).isoformat(),
                "dates": ["2026-11-25", "2026-11-26"],
            }
        ),
        encoding="utf-8",
    )

    cal = TradingCalendar(cache_file=cache_file, timezone="Asia/Shanghai")
    # Wednesday in cache -> trading day
    assert cal.is_trading_day(date(2026, 11, 25)) is True
    # Friday NOT in cache (holiday) -> non-trading day
    assert cal.is_trading_day(date(2026, 11, 27)) is False
    # Saturday -> never trading day
    assert cal.is_trading_day(date(2026, 11, 28)) is False


def test_build_scheduler_registers_all_half_hour_trading_slots(tmp_path: Path) -> None:
    settings = Settings(state_dir=tmp_path)
    pipeline = MonitorPipeline(settings)
    scheduler = build_scheduler(settings=settings, pipeline=pipeline, mock=True, dry_run=True)

    jobs = scheduler.get_jobs()
    assert len(jobs) == len(DEFAULT_SCHEDULE_TIMES) == 8

    job_ids = {job.id for job in jobs}
    expected_ids = {
        "a_stock_monitor_10_00",
        "a_stock_monitor_10_30",
        "a_stock_monitor_11_00",
        "a_stock_monitor_11_30",
        "a_stock_monitor_13_30",
        "a_stock_monitor_14_00",
        "a_stock_monitor_14_30",
        "a_stock_monitor_15_00",
    }
    assert job_ids == expected_ids
