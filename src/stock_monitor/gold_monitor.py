"""Gold price monitoring: Shanghai Gold (Au99.99), COMEX, London Gold, FX & alerts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from .ak_compat import ak_call
from .config import Settings
from .data_fetcher import _retry, _to_float, pick_col
from .models import (
    GoldAlert,
    GoldAlertType,
    GoldQuote,
    GoldSnapshot,
    PriceLevel,
)

logger = logging.getLogger(__name__)

GRAMS_PER_TROY_OUNCE = 31.1034768


def is_gold_market_open(now: datetime) -> bool:
    """Return True if global gold market is generally open (Beijing time).

    COMEX / London spot gold closes around Saturday 05:00~06:00 BJT and reopens
    Monday 06:00 BJT. We skip polling between Saturday 06:00 and Monday 06:00.
    """
    wd = now.weekday()  # Mon=0 .. Sun=6
    if wd == 6:  # Sunday
        return False
    if wd == 5 and now.hour >= 6:  # Saturday 06:00+
        return False
    if wd == 0 and now.hour < 6:  # Monday before 06:00
        return False
    return True


def compute_sge_30m_change_pct(rt_df: pd.DataFrame) -> Optional[float]:
    """Compute recent 30-minute % change from SGE 1-minute intraday quotes."""
    if rt_df is None or rt_df.empty or "现价" not in rt_df.columns:
        return None
    prices = pd.to_numeric(rt_df["现价"], errors="coerce").dropna()
    if len(prices) < 2:
        return None
    latest = float(prices.iloc[-1])
    # SGE realtime returns 1-minute rows; look back ~30 rows (or earliest available if >=10)
    if len(prices) >= 31:
        ref = float(prices.iloc[-31])
    elif len(prices) >= 10:
        ref = float(prices.iloc[0])
    else:
        return None
    if ref <= 0:
        return None
    return round((latest - ref) / ref * 100.0, 2)


def parse_usd_cny(fx_df: Optional[pd.DataFrame]) -> Optional[float]:
    """Extract USD/CNY mid rate from ``ak.fx_spot_quote()``."""
    if fx_df is None or fx_df.empty:
        return None
    pair_col = pick_col(fx_df, ["货币对", "货币名称", "名称"])
    bid_col = pick_col(fx_df, ["买报价", "买入价"])
    ask_col = pick_col(fx_df, ["卖报价", "卖出价"])
    if not pair_col:
        return None
    for _, row in fx_df.iterrows():
        pair = str(row.get(pair_col, "")).strip().upper()
        if "USD/CNY" in pair or "USDCNY" in pair:
            bid = _to_float(row.get(bid_col)) if bid_col else 0.0
            ask = _to_float(row.get(ask_col)) if ask_col else 0.0
            if bid > 0 and ask > 0:
                return round((bid + ask) / 2.0, 4)
            if bid > 0:
                return round(bid, 4)
            if ask > 0:
                return round(ask, 4)
    return None


def parse_foreign_gold_quotes(
    df: Optional[pd.DataFrame], usd_cny: Optional[float]
) -> List[GoldQuote]:
    """Parse COMEX Gold (GC) & London Gold (XAU) from Sina foreign futures."""
    if df is None or df.empty:
        return []
    quotes: List[GoldQuote] = []
    for _, row in df.iterrows():
        name = str(row.get("名称", "")).strip()
        if not name:
            continue
        if "COMEX" in name.upper() or "纽约" in name:
            key = "COMEX"
        elif "伦敦" in name or "XAU" in name.upper():
            key = "LONDON"
        else:
            key = name.upper()

        price = _to_float(row.get("最新价"))
        if price <= 0:
            continue
        prev_close = _to_float(row.get("昨日结算价")) or None
        change_pct = _to_float(row.get("涨跌幅"), default=float("nan"))
        if pd.isna(change_pct):
            change_pct = (
                round((price - prev_close) / prev_close * 100.0, 2)
                if prev_close and prev_close > 0
                else None
            )
        else:
            change_pct = round(change_pct, 2)

        cny_per_gram = _to_float(row.get("人民币报价")) or None
        if cny_per_gram is None and usd_cny and usd_cny > 0:
            cny_per_gram = price * usd_cny / GRAMS_PER_TROY_OUNCE
        if cny_per_gram is not None:
            cny_per_gram = round(cny_per_gram, 2)

        q_date = str(row.get("日期", "")).strip()
        q_time = str(row.get("行情时间", "")).strip()
        quote_time = f"{q_date} {q_time}".strip()

        quotes.append(
            GoldQuote(
                key=key,
                name=name,
                price=round(price, 2),
                unit="美元/盎司",
                prev_close=round(prev_close, 2) if prev_close else None,
                change_pct=change_pct,
                high=round(_to_float(row.get("最高价")), 2) or None,
                low=round(_to_float(row.get("最低价")), 2) or None,
                cny_per_gram=cny_per_gram,
                quote_time=quote_time,
            )
        )
    return quotes


class GoldDataFetcher:
    """Fetches Shanghai Gold Au99.99, COMEX Gold, London Gold, and USD/CNY."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)

    def fetch_snapshot(self) -> GoldSnapshot:
        now = datetime.now(self.tz)
        warnings: List[str] = []
        quotes: List[GoldQuote] = []

        # 1) USD/CNY exchange rate
        usd_cny: Optional[float] = None
        try:
            fx_df = _retry(lambda: ak_call("fx_spot_quote"), retries=2, label="fx_spot_quote")
            usd_cny = parse_usd_cny(fx_df)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"美元兑人民币汇率获取失败: {str(exc)[:80]}")

        # 2) Shanghai Gold Au99.99
        au_quote = self._fetch_sge_au9999(warnings)
        if au_quote is not None:
            quotes.append(au_quote)

        # 3) COMEX Gold & London Gold
        try:
            foreign_df = _retry(
                lambda: ak_call("futures_foreign_realtime_sina", symbol=["GC", "XAU"]),
                retries=2,
                label="futures_foreign_realtime_sina",
            )
            quotes.extend(parse_foreign_gold_quotes(foreign_df, usd_cny))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"COMEX/伦敦金行情获取失败: {str(exc)[:80]}")

        # Compute domestic vs international premium (元/克)
        premium: Optional[float] = None
        au = next((q for q in quotes if q.key == "AU9999"), None)
        intl = next((q for q in quotes if q.key == "LONDON"), None) or next(
            (q for q in quotes if q.key == "COMEX"), None
        )
        if au and intl and intl.cny_per_gram:
            premium = round(au.price - intl.cny_per_gram, 2)

        return GoldSnapshot(
            timestamp=now,
            quotes=quotes,
            usd_cny=usd_cny,
            premium_cny_per_gram=premium,
            is_mock=False,
            warnings=warnings,
        )

    def _fetch_sge_au9999(self, warnings: List[str]) -> Optional[GoldQuote]:
        rt_df: Optional[pd.DataFrame] = None
        try:
            rt_df = _retry(
                lambda: ak_call("spot_realtime_sge", symbol="Au99.99"),
                retries=2,
                label="spot_realtime_sge(Au99.99)",
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"上海金 Au99.99 实时行情获取失败: {str(exc)[:80]}")
            return None

        if rt_df is None or rt_df.empty or "现价" not in rt_df.columns:
            warnings.append("上海金 Au99.99 实时行情为空")
            return None

        prices = pd.to_numeric(rt_df["现价"], errors="coerce").dropna()
        if prices.empty:
            return None

        latest = round(float(prices.iloc[-1]), 2)
        high = round(float(prices.max()), 2)
        low = round(float(prices.min()), 2)
        change_30m = compute_sge_30m_change_pct(rt_df)

        quote_time = ""
        if "更新时间" in rt_df.columns:
            quote_time = str(rt_df["更新时间"].iloc[-1]).strip()
        elif "时间" in rt_df.columns:
            quote_time = str(rt_df["时间"].iloc[-1]).strip()

        # Fetch previous close from SGE historical daily data
        prev_close: Optional[float] = None
        try:
            hist_df = _retry(
                lambda: ak_call("spot_hist_sge", symbol="Au99.99"),
                retries=2,
                label="spot_hist_sge(Au99.99)",
            )
            if hist_df is not None and not hist_df.empty and "close" in hist_df.columns:
                hist_df = hist_df.dropna(subset=["close"]).reset_index(drop=True)
                today_iso = datetime.now(self.tz).date().isoformat()
                if "date" in hist_df.columns and str(hist_df["date"].iloc[-1]) == today_iso and len(hist_df) >= 2:
                    prev_close = round(float(hist_df["close"].iloc[-2]), 2)
                else:
                    prev_close = round(float(hist_df["close"].iloc[-1]), 2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("spot_hist_sge(Au99.99) failed: %s", exc)

        change_pct: Optional[float] = None
        if prev_close and prev_close > 0:
            change_pct = round((latest - prev_close) / prev_close * 100.0, 2)

        return GoldQuote(
            key="AU9999",
            name="上海金 Au99.99",
            price=latest,
            unit="元/克",
            prev_close=prev_close,
            change_pct=change_pct,
            high=high,
            low=low,
            cny_per_gram=latest,
            change_30m_pct=change_30m,
            quote_time=quote_time,
        )


# ---------------------------------------------------------------------- #
# State persistence & alert engine
# ---------------------------------------------------------------------- #
class GoldStateStore:
    """Persists rolling price history (for 30m move) & alert cooldown timestamps."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file

    def load(self) -> Dict:
        if not self.state_file.exists():
            return {"history": {}, "alerts": {}}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            data.setdefault("history", {})
            data.setdefault("alerts", {})
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read gold state %s: %s", self.state_file, exc)
            return {"history": {}, "alerts": {}}

    def save(self, data: Dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class GoldAlertEngine:
    """Detects 30-minute sharp moves, daily threshold moves, and price-level crosses."""

    def __init__(
        self,
        settings: Settings,
        state_store: Optional[GoldStateStore] = None,
    ) -> None:
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)
        self.state_store = state_store or GoldStateStore(settings.gold_state_file)

    def evaluate(
        self,
        snapshot: GoldSnapshot,
        persist: bool = True,
    ) -> Tuple[List[GoldAlert], List[GoldAlert]]:
        """Evaluate alerts on ``snapshot``.

        Returns ``(active_alerts, suppressed_alerts)``. Also enriches
        ``quote.change_30m_pct`` from rolling local history when not already set.
        """
        now = snapshot.timestamp
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.tz)

        state = self.state_store.load()
        history: Dict[str, List[Dict]] = state.get("history", {})
        alert_times: Dict[str, str] = state.get("alerts", {})

        # 1) Populate 30m change from rolling history if quote doesn't already have it
        for quote in snapshot.quotes:
            if quote.change_30m_pct is None:
                quote.change_30m_pct = self._calc_30m_from_history(
                    history.get(quote.key, []), now, quote.price
                )

        # 2) Detect candidate alerts
        candidates: List[GoldAlert] = []
        t30 = self.settings.gold_alert_30m_pct
        tdaily = self.settings.gold_alert_daily_pct

        for q in snapshot.quotes:
            if q.change_30m_pct is not None and abs(q.change_30m_pct) >= t30:
                if q.change_30m_pct > 0:
                    candidates.append(
                        GoldAlert(
                            alert_type=GoldAlertType.FAST_UP,
                            key=q.key,
                            message=(
                                f"{q.name} 近30分钟急涨 {q.change_30m_pct:+.2f}%，"
                                f"现报 {q.price:.2f} {q.unit}"
                            ),
                            dedupe_key=f"{q.key}:{GoldAlertType.FAST_UP.value}",
                        )
                    )
                else:
                    candidates.append(
                        GoldAlert(
                            alert_type=GoldAlertType.FAST_DOWN,
                            key=q.key,
                            message=(
                                f"{q.name} 近30分钟急跌 {q.change_30m_pct:+.2f}%，"
                                f"现报 {q.price:.2f} {q.unit}"
                            ),
                            dedupe_key=f"{q.key}:{GoldAlertType.FAST_DOWN.value}",
                        )
                    )

            if q.change_pct is not None and abs(q.change_pct) >= tdaily:
                if q.change_pct > 0:
                    candidates.append(
                        GoldAlert(
                            alert_type=GoldAlertType.DAILY_UP,
                            key=q.key,
                            message=(
                                f"{q.name} 日内大涨 {q.change_pct:+.2f}%，"
                                f"现报 {q.price:.2f} {q.unit}"
                            ),
                            dedupe_key=f"{q.key}:{GoldAlertType.DAILY_UP.value}",
                        )
                    )
                else:
                    candidates.append(
                        GoldAlert(
                            alert_type=GoldAlertType.DAILY_DOWN,
                            key=q.key,
                            message=(
                                f"{q.name} 日内大跌 {q.change_pct:+.2f}%，"
                                f"现报 {q.price:.2f} {q.unit}"
                            ),
                            dedupe_key=f"{q.key}:{GoldAlertType.DAILY_DOWN.value}",
                        )
                    )

        # 3) Check user-defined price levels
        for level in self.settings.gold_price_levels:
            alert = self._check_price_level(level, snapshot)
            if alert is not None:
                candidates.append(alert)

        # 4) Apply per-dedupe_key cooldown
        cooldown = timedelta(minutes=self.settings.gold_cooldown_minutes)
        active: List[GoldAlert] = []
        suppressed: List[GoldAlert] = []
        for alert in candidates:
            last_str = alert_times.get(alert.dedupe_key)
            in_cooldown = False
            if last_str:
                try:
                    last_dt = datetime.fromisoformat(last_str)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=self.tz)
                    if timedelta(0) <= (now - last_dt) < cooldown:
                        in_cooldown = True
                except ValueError:
                    pass
            if in_cooldown:
                suppressed.append(alert)
            else:
                active.append(alert)
                alert_times[alert.dedupe_key] = now.isoformat()

        # 5) Append current prices to rolling 2-hour history & persist
        if persist:
            cutoff = now - timedelta(hours=2)
            for q in snapshot.quotes:
                entries = history.get(q.key, [])
                kept = []
                for e in entries:
                    try:
                        ts = datetime.fromisoformat(e["ts"])
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=self.tz)
                        if ts >= cutoff:
                            kept.append(e)
                    except Exception:  # noqa: BLE001
                        continue
                kept.append({"ts": now.isoformat(), "price": q.price})
                history[q.key] = kept
            state["history"] = history
            state["alerts"] = alert_times
            self.state_store.save(state)

        return active, suppressed

    def _calc_30m_from_history(
        self, entries: List[Dict], now: datetime, current_price: float
    ) -> Optional[float]:
        if not entries or current_price <= 0:
            return None
        target = now - timedelta(minutes=30)
        best_price: Optional[float] = None
        best_delta: Optional[float] = None
        for entry in entries:
            try:
                ts = datetime.fromisoformat(entry["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=self.tz)
                price = float(entry["price"])
            except Exception:  # noqa: BLE001
                continue
            age_min = (now - ts).total_seconds() / 60.0
            # Accept reference points between 15 and 45 minutes ago, closest to 30m
            if 15.0 <= age_min <= 45.0 and price > 0:
                delta = abs((ts - target).total_seconds())
                if best_delta is None or delta < best_delta:
                    best_delta = delta
                    best_price = price
        if best_price is None or best_price <= 0:
            return None
        return round((current_price - best_price) / best_price * 100.0, 2)

    @staticmethod
    def _check_price_level(
        level: PriceLevel, snapshot: GoldSnapshot
    ) -> Optional[GoldAlert]:
        q = snapshot.get(level.key)
        if q is None:
            return None
        if level.direction == "above" and q.price >= level.price:
            return GoldAlert(
                alert_type=GoldAlertType.LEVEL_ABOVE,
                key=q.key,
                message=(
                    f"{q.name} 向上突破设定价位 {level.price:g} {q.unit}，"
                    f"现报 {q.price:.2f} {q.unit}"
                ),
                dedupe_key=f"level:{level.id}",
            )
        if level.direction == "below" and q.price <= level.price:
            return GoldAlert(
                alert_type=GoldAlertType.LEVEL_BELOW,
                key=q.key,
                message=(
                    f"{q.name} 向下跌破设定价位 {level.price:g} {q.unit}，"
                    f"现报 {q.price:.2f} {q.unit}"
                ),
                dedupe_key=f"level:{level.id}",
            )
        return None


# ---------------------------------------------------------------------- #
# Prompt & rule-based fallback
# ---------------------------------------------------------------------- #
GOLD_SYSTEM_PROMPT = """你是一位贵金属与宏观资产分析师，负责黄金盘中快评。
请严格依据用户提供的实时金价数据（上海金 Au99.99、COMEX 黄金、伦敦金、美元兑人民币汇率、国内外黄金价差、异动预警），输出简明扼要的 **3 段式黄金简评**（180~300字）：
**一、内外盘走势与价差**
**二、异动与关键位解读**
**三、关注要点**
不得编造未提供的数据，不做具体买卖建议，直接输出正文。"""


def format_gold_prompt(snapshot: GoldSnapshot, alerts: List[GoldAlert]) -> str:
    lines = [f"时间：{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"]
    for q in snapshot.quotes:
        cny = f"，折合人民币 {q.cny_per_gram:.2f} 元/克" if q.cny_per_gram and q.unit != "元/克" else ""
        chg = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "N/A"
        c30 = f"{q.change_30m_pct:+.2f}%" if q.change_30m_pct is not None else "N/A"
        lines.append(
            f"- {q.name}: 现价 {q.price:.2f} {q.unit} (日涨跌 {chg}，近30m {c30}{cny})"
        )
    if snapshot.usd_cny:
        lines.append(f"美元兑人民币：{snapshot.usd_cny:.4f}")
    if snapshot.premium_cny_per_gram is not None:
        lines.append(f"国内黄金溢价（Au99.99 - 国际折算）：{snapshot.premium_cny_per_gram:+.2f} 元/克")
    if alerts:
        lines.append("触发预警：" + "；".join(a.message for a in alerts))
    else:
        lines.append("当前状态：常规黄金简报（未触发异动预警）")
    return "\n".join(lines)


def build_rule_gold_summary(snapshot: GoldSnapshot, alerts: List[GoldAlert]) -> str:
    au = snapshot.get("AU9999")
    comex = snapshot.get("COMEX")
    london = snapshot.get("LONDON")

    parts = []
    if au:
        chg = f"（日内 {au.change_pct:+.2f}%）" if au.change_pct is not None else ""
        parts.append(f"上海金 Au99.99 报 {au.price:.2f} 元/克{chg}")
    if comex:
        chg = f"（日内 {comex.change_pct:+.2f}%）" if comex.change_pct is not None else ""
        parts.append(f"COMEX 黄金报 {comex.price:.2f} 美元/盎司{chg}")
    if london:
        chg = f"（日内 {london.change_pct:+.2f}%）" if london.change_pct is not None else ""
        parts.append(f"伦敦金报 {london.price:.2f} 美元/盎司{chg}")

    prem = ""
    if snapshot.premium_cny_per_gram is not None:
        state = "溢价" if snapshot.premium_cny_per_gram >= 0 else "折价"
        prem = f"国内较国际折算{state} {abs(snapshot.premium_cny_per_gram):.2f} 元/克。"

    alert_text = (
        "触发预警：" + "；".join(a.message for a in alerts) + "。"
        if alerts
        else "内外盘波动处于正常区间，暂未触发急涨急跌或关键价位预警。"
    )

    return (
        "**一、内外盘走势与价差**\n"
        f"{'；'.join(parts) or '暂无报价'}。{prem}\n\n"
        "**二、异动与关键位解读**\n"
        f"{alert_text}\n\n"
        "**三、关注要点**\n"
        "持续跟踪美元指数、美债收益率及汇率波动对人民币计价金价的叠加影响。（规则引擎摘要，仅供参考）"
    )
