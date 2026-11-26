"""Core monitoring pipelines: intraday A-share, daily close review, LHB, and gold."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from .alert_engine import AlertEngine
from .config import Settings
from .daily_review import DailyReviewFetcher, build_lhb_summary
from .data_fetcher import AKShareDataFetcher
from .feishu_notifier import FeishuNotifier
from .gold_monitor import GoldAlertEngine, GoldDataFetcher
from .llm_analyzer import MiniMaxAnalyzer
from .mock_data import (
    MockScenario,
    build_mock_daily_review,
    build_mock_gold_snapshot,
    build_mock_lhb_review,
    build_mock_snapshot,
)
from .models import (
    AlertResult,
    AnalysisResult,
    DailyReview,
    GoldAlert,
    GoldSnapshot,
    LhbReview,
    MarketSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineRunResult:
    """Output bundle of a single intraday monitoring cycle."""

    snapshot: MarketSnapshot
    alert: AlertResult
    analysis: AnalysisResult
    delivery: Dict[str, Any]


@dataclass
class ReviewRunResult:
    """Output bundle of a daily close review cycle."""

    review: DailyReview
    analysis: AnalysisResult
    delivery: Dict[str, Any]


@dataclass
class LhbRunResult:
    """Output bundle of a dragon-tiger list (龙虎榜) cycle."""

    lhb: LhbReview
    summary: str
    delivery: Dict[str, Any]


@dataclass
class GoldRunResult:
    """Output bundle of a gold price brief or alert-poll cycle."""

    snapshot: GoldSnapshot
    alerts: List[GoldAlert]
    suppressed_alerts: List[GoldAlert]
    analysis: Optional[AnalysisResult]
    delivery: Optional[Dict[str, Any]]
    sent: bool


class MonitorPipeline:
    """Orchestrates intraday monitoring, daily close review, LHB, and gold monitoring."""

    def __init__(
        self,
        settings: Settings,
        fetcher: Optional[AKShareDataFetcher] = None,
        alert_engine: Optional[AlertEngine] = None,
        analyzer: Optional[MiniMaxAnalyzer] = None,
        notifier: Optional[FeishuNotifier] = None,
        review_fetcher: Optional[DailyReviewFetcher] = None,
        gold_fetcher: Optional[GoldDataFetcher] = None,
        gold_alert_engine: Optional[GoldAlertEngine] = None,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher or AKShareDataFetcher(settings)
        self.alert_engine = alert_engine or AlertEngine(settings)
        self.analyzer = analyzer or MiniMaxAnalyzer(settings)
        self.notifier = notifier or FeishuNotifier(settings)
        self.review_fetcher = review_fetcher or DailyReviewFetcher(settings, self.fetcher)
        self.gold_fetcher = gold_fetcher or GoldDataFetcher(settings)
        self.gold_alert_engine = gold_alert_engine or GoldAlertEngine(settings)

    def run_once(
        self,
        mock: bool = False,
        mock_scenario: MockScenario = "normal",
        dry_run: bool = False,
    ) -> PipelineRunResult:
        """Execute a single intraday A-share monitoring pass."""
        logger.info(
            "开始执行 A 股盘中盯盘任务 (mock=%s, scenario=%s, dry_run=%s)",
            mock,
            mock_scenario,
            dry_run,
        )

        if mock:
            snapshot = build_mock_snapshot(
                scenario=mock_scenario, timezone=self.settings.timezone
            )
        else:
            snapshot = self.fetcher.fetch_snapshot()

        # Do not mutate persistent cooldown state during --dry-run
        alert = self.alert_engine.evaluate(snapshot, persist=not dry_run)
        logger.info(
            "量化预警评估完成: raw=%s, effective=%s, suppressed=%s",
            alert.raw_level.value,
            alert.effective_level.value,
            alert.is_suppressed_by_cooldown,
        )

        analysis = self.analyzer.analyze(snapshot, alert)
        logger.info(
            "解盘生成完成: source=%s, model=%s, latency=%dms",
            analysis.source,
            analysis.model,
            analysis.latency_ms,
        )

        delivery = self.notifier.send(
            snapshot=snapshot,
            alert=alert,
            analysis=analysis,
            dry_run=dry_run,
        )
        return PipelineRunResult(
            snapshot=snapshot,
            alert=alert,
            analysis=analysis,
            delivery=delivery,
        )

    def run_daily_review(
        self,
        mock: bool = False,
        mock_scenario: MockScenario = "normal",
        dry_run: bool = False,
    ) -> ReviewRunResult:
        """Execute post-close A-share daily review (15:30)."""
        logger.info(
            "开始执行 A 股收盘复盘任务 (mock=%s, scenario=%s, dry_run=%s)",
            mock,
            mock_scenario,
            dry_run,
        )
        if mock:
            review = build_mock_daily_review(
                scenario=mock_scenario, timezone=self.settings.timezone
            )
        else:
            review = self.review_fetcher.fetch_review()

        analysis = self.analyzer.analyze_review(review)
        logger.info(
            "收盘复盘生成完成: 情绪=%.1f(%s), source=%s, latency=%dms",
            review.sentiment.score,
            review.sentiment.label,
            analysis.source,
            analysis.latency_ms,
        )
        delivery = self.notifier.send_review(
            review=review, analysis=analysis, dry_run=dry_run
        )
        return ReviewRunResult(review=review, analysis=analysis, delivery=delivery)

    def run_lhb_review(
        self,
        mock: bool = False,
        dry_run: bool = False,
    ) -> LhbRunResult:
        """Execute post-close Dragon-Tiger List (龙虎榜) summary (18:00)."""
        logger.info("开始执行龙虎榜复盘任务 (mock=%s, dry_run=%s)", mock, dry_run)
        if mock:
            lhb = build_mock_lhb_review(timezone=self.settings.timezone)
        else:
            lhb = self.review_fetcher.fetch_lhb()

        summary = build_lhb_summary(lhb)
        delivery = self.notifier.send_lhb(lhb=lhb, summary=summary, dry_run=dry_run)
        return LhbRunResult(lhb=lhb, summary=summary, delivery=delivery)

    def run_gold_check(
        self,
        mode: Literal["brief", "poll"] = "brief",
        mock: bool = False,
        mock_scenario: MockScenario = "normal",
        dry_run: bool = False,
    ) -> GoldRunResult:
        """Execute a gold price check.

        - ``brief`` (09:30 / 21:00 or manual): always sends a Feishu card.
        - ``poll`` (every 5m): records price history and only sends on active alerts.
        """
        logger.info(
            "开始执行黄金监控任务 (mode=%s, mock=%s, scenario=%s, dry_run=%s)",
            mode,
            mock,
            mock_scenario,
            dry_run,
        )
        if mock:
            snapshot = build_mock_gold_snapshot(
                scenario=mock_scenario, timezone=self.settings.timezone
            )
        else:
            snapshot = self.gold_fetcher.fetch_snapshot()

        if not snapshot.quotes:
            logger.warning("黄金行情全部获取失败，跳过本次推送: %s", snapshot.warnings)
            return GoldRunResult(
                snapshot=snapshot,
                alerts=[],
                suppressed_alerts=[],
                analysis=None,
                delivery=None,
                sent=False,
            )

        alerts, suppressed = self.gold_alert_engine.evaluate(
            snapshot, persist=not dry_run
        )

        if mode == "poll" and not alerts:
            logger.debug(
                "黄金轮询未触发新预警（冷却抑制 %d 条），跳过推送", len(suppressed)
            )
            return GoldRunResult(
                snapshot=snapshot,
                alerts=alerts,
                suppressed_alerts=suppressed,
                analysis=None,
                delivery=None,
                sent=False,
            )

        analysis = self.analyzer.analyze_gold(snapshot, alerts)
        delivery = self.notifier.send_gold(
            snapshot=snapshot,
            alerts=alerts,
            analysis=analysis,
            dry_run=dry_run,
        )
        return GoldRunResult(
            snapshot=snapshot,
            alerts=alerts,
            suppressed_alerts=suppressed,
            analysis=analysis,
            delivery=delivery,
            sent=True,
        )
