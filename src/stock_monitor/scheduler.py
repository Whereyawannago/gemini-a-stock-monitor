"""APScheduler trading-calendar scheduler for 10:00~15:00 half-hour monitoring."""

from __future__ import annotations

import logging
import signal
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .calendar_utils import TradingCalendar
from .config import Settings
from .mock_data import MockScenario
from .pipeline import MonitorPipeline

logger = logging.getLogger(__name__)


def build_scheduler(
    settings: Settings,
    pipeline: MonitorPipeline,
    calendar: Optional[TradingCalendar] = None,
    mock: bool = False,
    mock_scenario: MockScenario = "normal",
    dry_run: bool = False,
) -> BlockingScheduler:
    """Create and configure a BlockingScheduler with 10:00~15:00 half-hour jobs."""
    tz = ZoneInfo(settings.timezone)
    cal = calendar or TradingCalendar(
        cache_file=settings.trade_calendar_cache_file,
        timezone=settings.timezone,
    )
    scheduler = BlockingScheduler(timezone=tz)

    def _scheduled_job() -> None:
        today = datetime.now(tz).date()
        if not mock and not cal.is_trading_day(today):
            logger.info("今日 %s 非 A 股交易日，跳过本次盯盘推送", today.isoformat())
            return
        try:
            pipeline.run_once(
                mock=mock,
                mock_scenario=mock_scenario,
                dry_run=dry_run,
            )
        except Exception:  # noqa: BLE001
            logger.exception("定时盯盘任务执行失败")

    for hour, minute in settings.schedule_times:
        job_id = f"a_stock_monitor_{hour:02d}_{minute:02d}"
        trigger = CronTrigger(
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            timezone=tz,
        )
        scheduler.add_job(
            _scheduled_job,
            trigger=trigger,
            id=job_id,
            name=f"A股半小时盯盘 ({hour:02d}:{minute:02d})",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    return scheduler


def run_scheduler(
    settings: Settings,
    pipeline: MonitorPipeline,
    mock: bool = False,
    mock_scenario: MockScenario = "normal",
    dry_run: bool = False,
) -> None:
    """Start the blocking APScheduler loop with graceful SIGINT/SIGTERM handling."""
    scheduler = build_scheduler(
        settings=settings,
        pipeline=pipeline,
        mock=mock,
        mock_scenario=mock_scenario,
        dry_run=dry_run,
    )

    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("收到退出信号 (%s)，正在停止定时调度器...", signum)
        if scheduler.running:
            scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    times_desc = ", ".join(f"{h:02d}:{m:02d}" for h, m in settings.schedule_times)
    logger.info(
        "A股半小时智能盯盘调度器已启动 (时区=%s, 触发时间=%s)",
        settings.timezone,
        times_desc,
    )
    scheduler.start()
