"""AKShare market data fetcher and intraday quantitative indicator calculator."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple, TypeVar
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import IndexSpec, Settings
from .models import (
    IndexSnapshot,
    MarketBreadth,
    MarketSnapshot,
    SectorFlowItem,
    SectorFlowSummary,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _retry(
    func: Callable[[], T],
    retries: int = 3,
    delay: float = 1.5,
    label: str = "akshare_call",
) -> T:
    """Execute a callable with simple linear backoff retries."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "%s failed (attempt %d/%d): %s", label, attempt, retries, exc
            )
            if attempt < retries:
                time.sleep(delay * attempt)
    assert last_exc is not None
    raise last_exc


def parse_chinese_amount_to_yi(value: object) -> float:
    """Parse numeric or Chinese-unit ('亿'/'万') flow values into 亿元 (1e8)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0.0
    if isinstance(value, (int, float, np.number)):
        val = float(value)
        # AKShare sector fund flow rank usually returns raw yuan (e.g. 1.2e9).
        # If magnitude > 1e5, treat as raw yuan and convert to 亿元.
        if abs(val) >= 1e5:
            return val / 1e8
        return val
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "nan", "None"}:
        return 0.0
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*([亿万]?)$", text)
    if match:
        num = float(match.group(1))
        unit = match.group(2)
        if unit == "亿":
            return num
        if unit == "万":
            return num / 1e4
        return num / 1e8 if abs(num) >= 1e5 else num
    try:
        num = float(text)
        return num / 1e8 if abs(num) >= 1e5 else num
    except ValueError:
        return 0.0


def _to_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float, np.number)):
        val = float(value)
        return default if np.isnan(val) else val
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text or text in {"-", "--", "nan", "None"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def normalize_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize 5-minute K-line DataFrame columns to standard English names."""
    if df is None or df.empty:
        return pd.DataFrame(
            columns=["datetime", "open", "high", "low", "close", "volume", "amount"]
        )

    rename_map = {
        "时间": "datetime",
        "日期": "datetime",
        "day": "datetime",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    norm = df.rename(columns=rename_map).copy()
    if "datetime" not in norm.columns:
        raise ValueError(f"K-line DataFrame missing datetime column: {list(df.columns)}")

    norm["datetime"] = pd.to_datetime(norm["datetime"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in norm.columns:
            norm[col] = pd.to_numeric(norm[col], errors="coerce").fillna(0.0)
        else:
            norm[col] = 0.0

    norm = norm.sort_values("datetime").reset_index(drop=True)
    return norm[["datetime", "open", "high", "low", "close", "volume", "amount"]]


def compute_intraday_vwap(today_df: pd.DataFrame) -> Optional[float]:
    """Compute intraday 5-minute VWAP in index point terms.

    For market indices, sum(amount) / sum(volume) gives average share price rather
    than index points. Therefore we compute the amount-weighted (or volume-weighted)
    average of each 5m bar's typical price (high + low + close) / 3.
    """
    if today_df is None or today_df.empty:
        return None

    has_hlc = (today_df["high"] > 0).any() and (today_df["low"] > 0).any()
    if has_hlc:
        typical_price = (today_df["high"] + today_df["low"] + today_df["close"]) / 3.0
    else:
        typical_price = today_df["close"]

    weights = today_df["amount"]
    if weights.sum() <= 0:
        weights = today_df["volume"]
    if weights.sum() <= 0:
        mean_val = float(typical_price.mean())
        return round(mean_val, 2) if mean_val > 0 else None

    vwap = float((typical_price * weights).sum() / weights.sum())
    return round(vwap, 2)


def compute_yoy_volume_ratio(
    today_df: pd.DataFrame, prev_df: pd.DataFrame
) -> Tuple[Optional[float], Optional[float]]:
    """Compare cumulative intraday amount up to current HH:MM vs previous trading day."""
    if today_df is None or today_df.empty or prev_df is None or prev_df.empty:
        return None, None

    latest_tod = today_df["datetime"].iloc[-1].strftime("%H:%M")
    prev_tods = prev_df["datetime"].dt.strftime("%H:%M")
    prev_slice = prev_df[prev_tods <= latest_tod]
    if prev_slice.empty:
        return None, None

    metric = "amount" if today_df["amount"].sum() > 0 and prev_slice["amount"].sum() > 0 else "volume"
    today_cum = float(today_df[metric].sum())
    prev_cum = float(prev_slice[metric].sum())
    if prev_cum <= 0:
        return None, None

    ratio = today_cum / prev_cum
    return round(ratio, 4), round((ratio - 1.0) * 100.0, 2)


def compute_marginal_30m_metrics(
    today_df: pd.DataFrame, prev_df: Optional[pd.DataFrame] = None
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Compute recent 30-minute (6 x 5m bars) turnover and marginal expansion ratio.

    Primary baseline: the exact same 30-minute time-of-day window on the previous
    trading day (eliminates A-share intraday U-shaped volume distortion).
    Fallback baseline: average per-bar turnover of earlier bars today.
    """
    if today_df is None or today_df.empty:
        return None, None, None

    recent = today_df.tail(6)
    recent_amount = float(recent["amount"].sum())
    metric = "amount" if recent_amount > 0 else "volume"
    recent_val = float(recent[metric].sum())
    if recent_val <= 0:
        return 0.0, None, None

    baseline_val: Optional[float] = None
    if prev_df is not None and not prev_df.empty:
        recent_tods = set(recent["datetime"].dt.strftime("%H:%M"))
        prev_matched = prev_df[prev_df["datetime"].dt.strftime("%H:%M").isin(recent_tods)]
        if not prev_matched.empty:
            prev_sum = float(prev_matched[metric].sum())
            if prev_sum > 0:
                # Scale if matched bar count differs slightly
                baseline_val = prev_sum * (len(recent) / len(prev_matched))

    if baseline_val is None and len(today_df) > len(recent):
        earlier = today_df.iloc[: -len(recent)]
        earlier_avg_per_bar = float(earlier[metric].mean())
        if earlier_avg_per_bar > 0:
            baseline_val = earlier_avg_per_bar * len(recent)

    if baseline_val is None or baseline_val <= 0:
        return round(recent_amount, 2), None, None

    ratio = recent_val / baseline_val
    return round(recent_amount, 2), round(ratio, 4), round((ratio - 1.0) * 100.0, 2)


def split_latest_two_trading_days(
    kline_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split a multi-day 5m K-line DataFrame into (latest_day_df, prev_day_df)."""
    if kline_df is None or kline_df.empty:
        empty = pd.DataFrame(
            columns=["datetime", "open", "high", "low", "close", "volume", "amount"]
        )
        return empty, empty

    dates = sorted(kline_df["datetime"].dt.date.unique())
    latest_date = dates[-1]
    today_df = kline_df[kline_df["datetime"].dt.date == latest_date].reset_index(drop=True)
    if len(dates) >= 2:
        prev_date = dates[-2]
        prev_df = kline_df[kline_df["datetime"].dt.date == prev_date].reset_index(drop=True)
    else:
        prev_df = pd.DataFrame(columns=kline_df.columns)
    return today_df, prev_df


class AKShareDataFetcher:
    """Fetches real-time A-share indices, 5m K-lines, breadth, and sector flows."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)

    def fetch_snapshot(self) -> MarketSnapshot:
        """Collect a complete intraday MarketSnapshot from AKShare."""
        import akshare as ak  # Lazy import so offline tests don't require network

        now = datetime.now(self.tz)
        warnings: List[str] = []

        spot_map = self._fetch_index_spot_map(ak, warnings)
        indices: List[IndexSnapshot] = []
        for spec in self.settings.indices:
            indices.append(self._build_index_snapshot(ak, spec, spot_map, now, warnings))

        breadth = self._fetch_market_breadth(ak, warnings)
        sector_flows = self._fetch_sector_flows(ak, warnings)

        return MarketSnapshot(
            timestamp=now,
            indices=indices,
            breadth=breadth,
            sector_flows=sector_flows,
            is_mock=False,
            warnings=warnings,
        )

    def _fetch_index_spot_map(
        self, ak: object, warnings: List[str]
    ) -> dict[str, dict[str, float]]:
        """Fetch real-time quotes for tracked indices via Eastmoney (Sina fallback)."""
        spot_map: dict[str, dict[str, float]] = {}
        target_codes = {spec.code for spec in self.settings.indices}

        try:
            frames = []
            for group in ("上证系列指数", "深证系列指数"):
                df = _retry(
                    lambda g=group: ak.stock_zh_index_spot_em(symbol=g),  # type: ignore[attr-defined]
                    retries=2,
                    label=f"index_spot_em({group})",
                )
                if df is not None and not df.empty:
                    frames.append(df)
            if frames:
                merged = pd.concat(frames, ignore_index=True)
                for _, row in merged.iterrows():
                    code = str(row.get("代码", "")).strip()
                    if code in target_codes:
                        spot_map[code] = {
                            "latest_price": _to_float(row.get("最新价")),
                            "prev_close": _to_float(row.get("昨收")),
                            "change_amount": _to_float(row.get("涨跌额")),
                            "change_pct": _to_float(row.get("涨跌幅")),
                            "today_volume": _to_float(row.get("成交量")),
                            "today_amount": _to_float(row.get("成交额")),
                        }
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"东方财富指数实时行情获取异常，尝试新浪备用源: {exc}")
            logger.warning("Eastmoney index spot failed: %s", exc)

        # Fallback to Sina for any missing index
        missing = target_codes - set(spot_map.keys())
        if missing:
            try:
                sina_df = _retry(
                    lambda: ak.stock_zh_index_spot_sina(),  # type: ignore[attr-defined]
                    retries=2,
                    label="index_spot_sina",
                )
                for _, row in sina_df.iterrows():
                    raw_code = str(row.get("代码", "")).strip()
                    digits = re.sub(r"^\D+", "", raw_code)
                    if digits in missing:
                        spot_map[digits] = {
                            "latest_price": _to_float(row.get("最新价")),
                            "prev_close": _to_float(row.get("昨收")),
                            "change_amount": _to_float(row.get("涨跌额")),
                            "change_pct": _to_float(row.get("涨跌幅")),
                            "today_volume": _to_float(row.get("成交量")),
                            "today_amount": _to_float(row.get("成交额")),
                        }
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"新浪指数实时行情获取失败: {exc}")
                logger.error("Sina index spot fallback failed: %s", exc)

        return spot_map

    def _build_index_snapshot(
        self,
        ak: object,
        spec: IndexSpec,
        spot_map: dict[str, dict[str, float]],
        now: datetime,
        warnings: List[str],
    ) -> IndexSnapshot:
        """Build a single IndexSnapshot combining spot quote and 5-minute K-lines."""
        spot = spot_map.get(spec.code, {})
        start_dt = (now - timedelta(days=10)).strftime("%Y-%m-%d 09:30:00")
        end_dt = now.strftime("%Y-%m-%d 15:05:00")

        today_df = pd.DataFrame()
        prev_df = pd.DataFrame()
        data_note = ""

        try:
            raw_kline = _retry(
                lambda: ak.index_zh_a_hist_min_em(  # type: ignore[attr-defined]
                    symbol=spec.code,
                    period="5",
                    start_date=start_dt,
                    end_date=end_dt,
                ),
                retries=2,
                label=f"5m_kline({spec.code})",
            )
            norm = normalize_kline_df(raw_kline)
            today_df, prev_df = split_latest_two_trading_days(norm)
        except Exception as exc:  # noqa: BLE001
            data_note = f"5m K线缺失: {exc}"
            warnings.append(f"{spec.name}({spec.code}) 5分钟K线获取失败: {exc}")
            logger.warning("5m kline failed for %s: %s", spec.code, exc)

        latest_price = spot.get("latest_price", 0.0)
        if latest_price <= 0 and not today_df.empty:
            latest_price = float(today_df["close"].iloc[-1])

        prev_close = spot.get("prev_close", 0.0)
        if prev_close <= 0 and not prev_df.empty:
            prev_close = float(prev_df["close"].iloc[-1])

        change_amount = spot.get("change_amount", 0.0)
        change_pct = spot.get("change_pct", 0.0)
        if change_pct == 0.0 and latest_price > 0 and prev_close > 0:
            change_amount = round(latest_price - prev_close, 2)
            change_pct = round(change_amount / prev_close * 100.0, 2)

        today_amount = spot.get("today_amount", 0.0)
        if today_amount <= 0 and not today_df.empty:
            today_amount = float(today_df["amount"].sum())

        today_volume = spot.get("today_volume", 0.0)
        if today_volume <= 0 and not today_df.empty:
            today_volume = float(today_df["volume"].sum())

        vwap = compute_intraday_vwap(today_df)
        vwap_diff_pct = (
            round((latest_price - vwap) / vwap * 100.0, 2)
            if vwap and vwap > 0 and latest_price > 0
            else None
        )
        yoy_ratio, yoy_diff_pct = compute_yoy_volume_ratio(today_df, prev_df)
        m30_amount, m30_ratio, m30_diff_pct = compute_marginal_30m_metrics(
            today_df, prev_df
        )

        return IndexSnapshot(
            code=spec.code,
            name=spec.name,
            symbol=spec.symbol,
            latest_price=round(latest_price, 2),
            prev_close=round(prev_close, 2),
            change_amount=round(change_amount, 2),
            change_pct=round(change_pct, 2),
            today_amount=round(today_amount, 2),
            today_volume=round(today_volume, 2),
            vwap=vwap,
            vwap_diff_pct=vwap_diff_pct,
            yoy_amount_ratio=yoy_ratio,
            yoy_amount_diff_pct=yoy_diff_pct,
            marginal_30m_amount=m30_amount,
            marginal_30m_ratio=m30_ratio,
            marginal_30m_diff_pct=m30_diff_pct,
            data_note=data_note,
        )

    def _fetch_market_breadth(
        self, ak: object, warnings: List[str]
    ) -> MarketBreadth:
        """Compute whole-market advance/decline counts and total turnover."""
        try:
            df = _retry(
                lambda: ak.stock_zh_a_spot_em(),  # type: ignore[attr-defined]
                retries=2,
                label="stock_zh_a_spot_em",
            )
            if df is None or df.empty:
                return MarketBreadth()

            pct = pd.to_numeric(df["涨跌幅"], errors="coerce").dropna()
            advancing = int((pct > 0).sum())
            declining = int((pct < 0).sum())
            unchanged = int((pct == 0).sum())
            limit_up = int((pct >= 9.9).sum())
            limit_down = int((pct <= -9.9).sum())

            total_turnover = None
            if "成交额" in df.columns:
                total_turnover = float(
                    pd.to_numeric(df["成交额"], errors="coerce").fillna(0.0).sum()
                )

            return MarketBreadth(
                advancing=advancing,
                declining=declining,
                unchanged=unchanged,
                limit_up=limit_up,
                limit_down=limit_down,
                total_turnover=total_turnover,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"全市场涨跌家数获取失败: {exc}")
            logger.warning("Market breadth fetch failed: %s", exc)
            return MarketBreadth()

    def _fetch_sector_flows(
        self, ak: object, warnings: List[str]
    ) -> SectorFlowSummary:
        """Fetch industry and concept capital flows (top inflow & outflow)."""
        top_n = self.settings.sector_top_n
        ind_in, ind_out = self._fetch_single_sector_dimension(
            ak, "行业资金流", top_n, warnings
        )
        con_in, con_out = self._fetch_single_sector_dimension(
            ak, "概念资金流", top_n, warnings
        )
        return SectorFlowSummary(
            industry_inflow_top=ind_in,
            industry_outflow_top=ind_out,
            concept_inflow_top=con_in,
            concept_outflow_top=con_out,
        )

    def _fetch_single_sector_dimension(
        self,
        ak: object,
        sector_type: str,
        top_n: int,
        warnings: List[str],
    ) -> Tuple[List[SectorFlowItem], List[SectorFlowItem]]:
        try:
            df = _retry(
                lambda: ak.stock_sector_fund_flow_rank(  # type: ignore[attr-defined]
                    indicator="今日", sector_type=sector_type
                ),
                retries=2,
                label=f"sector_flow({sector_type})",
            )
            if df is None or df.empty:
                return [], []

            name_col = self._pick_col(df, ["名称", "行业", "板块名称"])
            pct_col = self._pick_col(df, ["今日涨跌幅", "涨跌幅"])
            inflow_col = self._pick_col(
                df, ["今日主力净流入-净额", "主力净流入-净额", "净额"]
            )
            ratio_col = self._pick_col(
                df, ["今日主力净流入-净占比", "主力净流入-净占比", "净占比"]
            )
            leader_col = self._pick_col(
                df, ["今日主力净流入最大股", "领涨股", "主力净流入最大股"]
            )

            if not name_col or not inflow_col:
                warnings.append(f"{sector_type} 字段结构变更，无法解析资金流向")
                return [], []

            items: List[SectorFlowItem] = []
            for _, row in df.iterrows():
                name = str(row.get(name_col, "")).strip()
                if not name:
                    continue
                items.append(
                    SectorFlowItem(
                        name=name,
                        change_pct=round(_to_float(row.get(pct_col)), 2)
                        if pct_col
                        else 0.0,
                        net_inflow=round(
                            parse_chinese_amount_to_yi(row.get(inflow_col)), 2
                        ),
                        net_inflow_ratio=round(_to_float(row.get(ratio_col)), 2)
                        if ratio_col
                        else None,
                        leader_stock=str(row.get(leader_col, "")).strip()
                        if leader_col
                        else "",
                    )
                )

            items_sorted = sorted(items, key=lambda x: x.net_inflow, reverse=True)
            inflow_top = items_sorted[:top_n]
            outflow_top = sorted(items, key=lambda x: x.net_inflow)[:top_n]
            return inflow_top, outflow_top
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{sector_type} 获取失败: {exc}")
            logger.warning("Sector flow fetch failed (%s): %s", sector_type, exc)
            return [], []

    @staticmethod
    def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
        for col in candidates:
            if col in df.columns:
                return col
        return None
