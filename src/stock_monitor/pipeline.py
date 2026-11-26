"""Core monitoring pipeline orchestrating data fetch, alert, LLM, and Feishu."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .alert_engine import AlertEngine
from .config import Settings
from .data_fetcher import AKShareDataFetcher
from .feishu_notifier import FeishuNotifier
from .llm_analyzer import MiniMaxAnalyzer
from .mock_data import MockScenario, build_mock_snapshot
from .models import AlertResult, AnalysisResult, MarketSnapshot

logger = logging.getLogger(__name__)


@dataclass
class PipelineRunResult:
    """Output bundle of a single monitoring cycle."""

    snapshot: MarketSnapshot
    alert: AlertResult
    analysis: AnalysisResult
    delivery: Dict[str, Any]


class MonitorPipeline:
    """Orchestrates one complete 30-minute A-share monitoring pass."""

    def __init__(
        self,
        settings: Settings,
        fetcher: Optional[AKShareDataFetcher] = None,
        alert_engine: Optional[AlertEngine] = None,
        analyzer: Optional[MiniMaxAnalyzer] = None,
        notifier: Optional[FeishuNotifier] = None,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher or AKShareDataFetcher(settings)
        self.alert_engine = alert_engine or AlertEngine(settings)
        self.analyzer = analyzer or MiniMaxAnalyzer(settings)
        self.notifier = notifier or FeishuNotifier(settings)

    def run_once(
        self,
        mock: bool = False,
        mock_scenario: MockScenario = "normal",
        dry_run: bool = False,
    ) -> PipelineRunResult:
        """Execute a single monitoring pass."""
        logger.info(
            "开始执行 A 股盯盘任务 (mock=%s, scenario=%s, dry_run=%s)",
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
