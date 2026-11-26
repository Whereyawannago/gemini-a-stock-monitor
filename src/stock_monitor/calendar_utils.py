"""A-share trading calendar helper with AKShare cache and weekday fallback."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Set
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class TradingCalendar:
    """Checks whether a given date is an A-share trading day."""

    def __init__(
        self,
        cache_file: Path,
        timezone: str = "Asia/Shanghai",
        cache_ttl_days: int = 7,
    ) -> None:
        self.cache_file = cache_file
        self.tz = ZoneInfo(timezone)
        self.cache_ttl_days = cache_ttl_days
        self._trade_dates: Optional[Set[str]] = None

    def is_trading_day(self, target_date: Optional[date] = None) -> bool:
        """Return True if target_date is an A-share trading day."""
        d = target_date or datetime.now(self.tz).date()
        # Weekends are never A-share trading days
        if d.weekday() >= 5:
            return False

        dates = self._get_trade_dates()
        if dates:
            return d.isoformat() in dates

        # Fallback: weekdays when offline / calendar unavailable
        logger.warning(
            "交易日历不可用，降级为工作日判断: %s -> True", d.isoformat()
        )
        return True

    def _get_trade_dates(self) -> Set[str]:
        if self._trade_dates is not None:
            return self._trade_dates

        cached = self._load_cache()
        if cached is not None:
            self._trade_dates = cached
            return cached

        fetched = self._fetch_from_akshare()
        if fetched:
            self._save_cache(fetched)
            self._trade_dates = fetched
            return fetched

        return set()

    def _load_cache(self) -> Optional[Set[str]]:
        if not self.cache_file.exists():
            return None
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(data["updated_at"])
            if datetime.now(self.tz) - updated_at <= timedelta(days=self.cache_ttl_days):
                dates = set(data.get("dates", []))
                if dates:
                    return dates
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取交易日历缓存失败: %s", exc)
        return None

    def _save_cache(self, dates: Set[str]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now(self.tz).isoformat(),
                "dates": sorted(dates),
            }
            self.cache_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入交易日历缓存失败: %s", exc)

    def _fetch_from_akshare(self) -> Set[str]:
        try:
            import pandas as pd

            from .ak_compat import ak_call

            df = ak_call("calendar_trade_date_hist_sina")
            if df is None or df.empty:
                return set()
            col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            return {dt.date().isoformat() for dt in parsed}
        except Exception as exc:  # noqa: BLE001
            logger.warning("从 AKShare 获取 A 股交易日历失败: %s", exc)
            return set()
