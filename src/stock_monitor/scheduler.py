"""APScheduler setup for A-share intraday, close review, LHB, and gold monitoring."""

from __future__ import annotations

import logging
import signal
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .calendar_utils import TradingCalendar
from .config import Settings
from .gold_monitor import is_gold_market_open
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
    """Create and configure BlockingScheduler for all enabled monitoring jobs."""
    tz = ZoneInfo(settings.timezone)
    cal = calendar or TradingCalendar(
        cache_file=settings.trade_calendar_cache_file,
        timezone=settings.timezone,
    )
    scheduler = BlockingScheduler(timezone=tz)

    def _is_a_share_trading_today() -> bool:
        if mock:
            return True
        today = datetime.now(tz).date()
        if not cal.is_trading_day(today):
            logger.info("今日 %s 非 A 股交易日，跳过本次 A 股任务", today.isoformat())
            return False
        return True

    # 1) A 股盘中半小时盯盘（10:00 ~ 15:00，交易日）
    if settings.enable_intraday_monitor:
        def _intraday_job() -> None:
            if not _is_a_share_trading_today():
                return
            try:
                pipeline.run_once(
                    mock=mock, mock_scenario=mock_scenario, dry_run=dry_run
                )
            except Exception:  # noqa: BLE001
                logger.exception("盘中盯盘任务执行失败")

        for hour, minute in settings.schedule_times:
            scheduler.add_job(
                _intraday_job,
                trigger=CronTrigger(
                    day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz
                ),
                id=f"a_stock_monitor_{hour:02d}_{minute:02d}",
                name=f"A股半小时盯盘 ({hour:02d}:{minute:02d})",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )

    # 2) A 股每日收盘复盘（默认 15:30，交易日）
    if settings.enable_daily_review:
        def _review_job() -> None:
            if not _is_a_share_trading_today():
                return
            try:
                pipeline.run_daily_review(
                    mock=mock, mock_scenario=mock_scenario, dry_run=dry_run
                )
            except Exception:  # noqa: BLE001
                logger.exception("每日收盘复盘任务执行失败")

        rh, rm = settings.review_time
        scheduler.add_job(
            _review_job,
            trigger=CronTrigger(
                day_of_week="mon-fri", hour=rh, minute=rm, timezone=tz
            ),
            id="a_stock_daily_review",
            name=f"A股收盘复盘 ({rh:02d}:{rm:02d})",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )

    # 3) A 股龙虎榜复盘（默认 18:00，交易日）
    if settings.enable_lhb_review:
        def _lhb_job() -> None:
            if not _is_a_share_trading_today():
                return
            try:
                pipeline.run_lhb_review(mock=mock, dry_run=dry_run)
            except Exception:  # noqa: BLE001
                logger.exception("龙虎榜复盘任务执行失败")

        lh, lm = settings.lhb_time
        scheduler.add_job(
            _lhb_job,
            trigger=CronTrigger(
                day_of_week="mon-fri", hour=lh, minute=lm, timezone=tz
            ),
            id="a_stock_lhb_review",
            name=f"A股龙虎榜复盘 ({lh:02d}:{lm:02d})",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )

    # 4) 黄金定时简报（默认 09:30 / 21:00，周一至周五）与异动/价位轮询（每 5 分钟）
    if settings.enable_gold_monitor:
        def _gold_brief_job() -> None:
            try:
                pipeline.run_gold_check(
                    mode="brief",
                    mock=mock,
                    mock_scenario=mock_scenario,
                    dry_run=dry_run,
                )
            except Exception:  # noqa: BLE001
                logger.exception("黄金定时简报任务执行失败")

        for gh, gm in settings.gold_brief_times:
            scheduler.add_job(
                _gold_brief_job,
                trigger=CronTrigger(
                    day_of_week="mon-fri", hour=gh, minute=gm, timezone=tz
                ),
                id=f"gold_brief_{gh:02d}_{gm:02d}",
                name=f"黄金定时简报 ({gh:02d}:{gm:02d})",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
            )

        def _gold_poll_job() -> None:
            now = datetime.now(tz)
            if not mock and not is_gold_market_open(now):
                return
            try:
                pipeline.run_gold_check(
                    mode="poll",
                    mock=mock,
                    mock_scenario=mock_scenario,
                    dry_run=dry_run,
                )
            except Exception:  # noqa: BLE001
                logger.exception("黄金异动轮询任务执行失败")

        scheduler.add_job(
            _gold_poll_job,
            trigger=IntervalTrigger(minutes=settings.gold_poll_minutes, timezone=tz),
            id="gold_alert_poll",
            name=f"黄金异动/价位监控 (每{settings.gold_poll_minutes}分钟)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
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

    jobs_desc = ", ".join(job.name for job in scheduler.get_jobs())
    logger.info(
        "智能盯盘调度器已启动 (时区=%s, 共 %d 个任务: %s)",
        settings.timezone,
        len(scheduler.get_jobs()),
        jobs_desc,
    )
    scheduler.start()
