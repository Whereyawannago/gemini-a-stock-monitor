"""Quantitative trend alert engine with 60-minute anti-spam cooldown."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from .config import Settings
from .models import AlertLevel, AlertResult, MarketSnapshot

logger = logging.getLogger(__name__)


class AlertStateStore:
    """Persists the most recent alert trigger timestamp and direction to JSON."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file

    def load(self) -> dict:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read alert state %s: %s", self.state_file, exc)
            return {}

    def save(self, level: AlertLevel, triggered_at: datetime) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_alert_level": level.value,
            "last_alert_time": triggered_at.isoformat(),
        }
        self.state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class AlertEngine:
    """Evaluates volume_surge / volume_plunge alerts with cooldown suppression."""

    def __init__(
        self,
        settings: Settings,
        state_store: Optional[AlertStateStore] = None,
    ) -> None:
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)
        self.state_store = state_store or AlertStateStore(settings.alert_state_file)

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        persist: bool = True,
    ) -> AlertResult:
        """Classify market trend and apply 60-minute same-direction cooldown."""
        now = snapshot.timestamp
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.tz)

        raw_level, score, reasons = self._detect_raw_signal(snapshot)

        if raw_level == AlertLevel.NORMAL:
            return AlertResult(
                raw_level=AlertLevel.NORMAL,
                effective_level=AlertLevel.NORMAL,
                is_suppressed_by_cooldown=False,
                cooldown_remaining_minutes=0,
                score=score,
                reasons=reasons,
                summary_tag="常规简报",
            )

        # Check 60-minute anti-spam cooldown for same-direction alerts.
        # Trend reversals (e.g. surge -> plunge) bypass cooldown immediately.
        state = self.state_store.load()
        last_level_str = state.get("last_alert_level")
        last_time_str = state.get("last_alert_time")

        if last_level_str == raw_level.value and last_time_str:
            try:
                last_time = datetime.fromisoformat(last_time_str)
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=self.tz)
                cooldown = timedelta(minutes=self.settings.alert_cooldown_minutes)
                elapsed = now - last_time
                if timedelta(0) <= elapsed < cooldown:
                    remaining_sec = (cooldown - elapsed).total_seconds()
                    remaining_min = max(1, math.ceil(remaining_sec / 60.0))
                    reasons.append(
                        f"同类预警处于 {self.settings.alert_cooldown_minutes} 分钟冷却期"
                        f"（剩余约 {remaining_min} 分钟），本次降级为常规简报"
                    )
                    return AlertResult(
                        raw_level=raw_level,
                        effective_level=AlertLevel.NORMAL,
                        is_suppressed_by_cooldown=True,
                        cooldown_remaining_minutes=remaining_min,
                        score=score,
                        reasons=reasons,
                        summary_tag="常规简报（预警冷却中）",
                    )
            except ValueError:
                logger.warning("Invalid last_alert_time in state file: %s", last_time_str)

        if persist:
            self.state_store.save(raw_level, now)

        tag = (
            "放量上涨预警"
            if raw_level == AlertLevel.VOLUME_SURGE
            else "放量下跌预警"
        )
        return AlertResult(
            raw_level=raw_level,
            effective_level=raw_level,
            is_suppressed_by_cooldown=False,
            cooldown_remaining_minutes=0,
            score=score,
            reasons=reasons,
            summary_tag=tag,
        )

    def _detect_raw_signal(
        self, snapshot: MarketSnapshot
    ) -> tuple[AlertLevel, float, List[str]]:
        """Combine volume ratio, marginal 30m volume, VWAP, and breadth."""
        indices = snapshot.indices
        if not indices:
            return AlertLevel.NORMAL, 0.0, ["指数数据为空，维持常规简报"]

        avg_change_pct = sum(i.change_pct for i in indices) / len(indices)

        yoy_ratios = [
            i.yoy_amount_ratio for i in indices if i.yoy_amount_ratio is not None
        ]
        avg_yoy_ratio = sum(yoy_ratios) / len(yoy_ratios) if yoy_ratios else None

        marginal_ratios = [
            i.marginal_30m_ratio for i in indices if i.marginal_30m_ratio is not None
        ]
        avg_marginal_ratio = (
            sum(marginal_ratios) / len(marginal_ratios) if marginal_ratios else None
        )

        vwap_diffs = [i.vwap_diff_pct for i in indices if i.vwap_diff_pct is not None]
        above_vwap_count = sum(1 for d in vwap_diffs if d > 0)
        below_vwap_count = sum(1 for d in vwap_diffs if d < 0)
        avg_vwap_diff = sum(vwap_diffs) / len(vwap_diffs) if vwap_diffs else 0.0

        advance_ratio = snapshot.breadth.advance_ratio
        has_breadth = snapshot.breadth.total > 0

        # Volume expansion check (cumulative YoY OR recent 30m marginal surge)
        yoy_expanded = (
            avg_yoy_ratio is not None
            and avg_yoy_ratio >= self.settings.alert_volume_ratio_threshold
        )
        marginal_expanded = (
            avg_marginal_ratio is not None
            and avg_marginal_ratio >= self.settings.alert_marginal_ratio_threshold
        )
        is_volume_expanded = yoy_expanded or marginal_expanded

        reasons: List[str] = []
        if avg_yoy_ratio is not None:
            reasons.append(
                f"累计量能同比昨日同时刻均值 {avg_yoy_ratio:.2f}x"
                f"（{(avg_yoy_ratio - 1) * 100:+.1f}%）"
            )
        if avg_marginal_ratio is not None:
            reasons.append(
                f"近30分钟边际量能均值 {avg_marginal_ratio:.2f}x"
                f"（{(avg_marginal_ratio - 1) * 100:+.1f}%）"
            )
        if vwap_diffs:
            reasons.append(
                f"VWAP偏离均值 {avg_vwap_diff:+.2f}%"
                f"（站上 {above_vwap_count}/{len(vwap_diffs)}，跌破 {below_vwap_count}/{len(vwap_diffs)}）"
            )
        if has_breadth:
            reasons.append(
                f"全市场上涨家数占比 {advance_ratio * 100:.1f}%"
                f"（涨{snapshot.breadth.advancing}/跌{snapshot.breadth.declining}）"
            )

        # Bullish volume surge check
        vwap_bullish = (not vwap_diffs) or (
            above_vwap_count >= max(2, len(vwap_diffs) - 1) and avg_vwap_diff > 0
        )
        breadth_bullish = (not has_breadth) or (
            advance_ratio >= self.settings.alert_surge_advance_ratio
        )
        price_bullish = avg_change_pct >= self.settings.alert_surge_change_pct

        if is_volume_expanded and price_bullish and vwap_bullish and breadth_bullish:
            score = round(
                avg_change_pct * 10
                + ((avg_yoy_ratio or 1.0) - 1.0) * 50
                + ((avg_marginal_ratio or 1.0) - 1.0) * 30
                + (advance_ratio - 0.5) * 40,
                2,
            )
            reasons.insert(
                0,
                f"触发【放量上涨】预警：四大指数均值 {avg_change_pct:+.2f}%，量价与广度共振走强",
            )
            return AlertLevel.VOLUME_SURGE, score, reasons

        # Bearish volume plunge check
        vwap_bearish = (not vwap_diffs) or (
            below_vwap_count >= max(2, len(vwap_diffs) - 1) and avg_vwap_diff < 0
        )
        breadth_bearish = (not has_breadth) or (
            advance_ratio <= self.settings.alert_plunge_advance_ratio
        )
        price_bearish = avg_change_pct <= self.settings.alert_plunge_change_pct

        if is_volume_expanded and price_bearish and vwap_bearish and breadth_bearish:
            score = round(
                avg_change_pct * 10
                - ((avg_yoy_ratio or 1.0) - 1.0) * 50
                - ((avg_marginal_ratio or 1.0) - 1.0) * 30
                - (0.5 - advance_ratio) * 40,
                2,
            )
            reasons.insert(
                0,
                f"触发【放量下跌】预警：四大指数均值 {avg_change_pct:+.2f}%，放量跌破分时均价且普跌",
            )
            return AlertLevel.VOLUME_PLUNGE, score, reasons

        reasons.insert(
            0,
            f"市场未触发极端放量趋势预警（四大指数均值 {avg_change_pct:+.2f}%），发送常规简报",
        )
        return AlertLevel.NORMAL, round(avg_change_pct * 10, 2), reasons
