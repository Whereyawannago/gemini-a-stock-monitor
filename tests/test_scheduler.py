"""Tests for trading calendar and APScheduler job registration."""

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
    assert cal.is_trading_day(date(2026, 11, 25)) is True
    assert cal.is_trading_day(date(2026, 11, 27)) is False
    assert cal.is_trading_day(date(2026, 11, 28)) is False


def test_build_scheduler_registers_intraday_review_lhb_and_gold_jobs(
    tmp_path: Path,
) -> None:
    settings = Settings(state_dir=tmp_path)
    pipeline = MonitorPipeline(settings)
    scheduler = build_scheduler(
        settings=settings, pipeline=pipeline, mock=True, dry_run=True
    )

    job_ids = {job.id for job in scheduler.get_jobs()}
    expected_intraday = {f"a_stock_monitor_{h:02d}_{m:02d}" for h, m in DEFAULT_SCHEDULE_TIMES}
    expected = expected_intraday | {
        "a_stock_daily_review",
        "a_stock_lhb_review",
        "gold_brief_09_30",
        "gold_brief_21_00",
        "gold_alert_poll",
    }
    assert job_ids == expected


def test_build_scheduler_respects_feature_toggles(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path,
        enable_intraday_monitor=False,
        enable_daily_review=True,
        enable_lhb_review=False,
        enable_gold_monitor=False,
    )
    scheduler = build_scheduler(
        settings=settings, pipeline=MonitorPipeline(settings), mock=True, dry_run=True
    )
    assert {job.id for job in scheduler.get_jobs()} == {"a_stock_daily_review"}
