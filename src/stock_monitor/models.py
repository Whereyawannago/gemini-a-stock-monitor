"""Domain data models for market snapshots, alerts, and LLM analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class AlertLevel(str, Enum):
    """Alert classification driving card theme color and notification urgency."""

    NORMAL = "normal"  # 常规蓝色简报
    VOLUME_SURGE = "volume_surge"  # 放量上涨红色预警
    VOLUME_PLUNGE = "volume_plunge"  # 放量下跌绿色预警


@dataclass
class IndexSnapshot:
    """Real-time & intraday 5m-derived metrics for a single index."""

    code: str
    name: str
    symbol: str
    latest_price: float
    prev_close: float
    change_amount: float
    change_pct: float  # percentage, e.g. 1.25 means +1.25%
    today_amount: float  # 累计成交额（元）
    today_volume: float  # 累计成交量（手/股，取决于源，计算同比时同口径比较）
    vwap: Optional[float] = None  # 当日 5m K 线分时均价 (VWAP)
    vwap_diff_pct: Optional[float] = None  # (latest_price - vwap) / vwap * 100
    yoy_amount_ratio: Optional[float] = None  # 累计量能同比昨日同时刻比值（如 1.25 表示 +25%）
    yoy_amount_diff_pct: Optional[float] = None  # (ratio - 1) * 100
    marginal_30m_amount: Optional[float] = None  # 近30分钟成交额（元）
    marginal_30m_ratio: Optional[float] = None  # 近30分钟边际放量倍数（相对前序同时段均值或昨日同时刻）
    marginal_30m_diff_pct: Optional[float] = None  # (marginal_ratio - 1) * 100
    data_note: str = ""


@dataclass
class MarketBreadth:
    """Whole-market advance/decline breadth statistics."""

    advancing: int = 0
    declining: int = 0
    unchanged: int = 0
    limit_up: int = 0
    limit_down: int = 0
    total_turnover: Optional[float] = None  # 两市/全市场成交额（元）

    @property
    def total(self) -> int:
        return self.advancing + self.declining + self.unchanged

    @property
    def advance_ratio(self) -> float:
        if self.total <= 0:
            return 0.5
        return self.advancing / self.total

    @property
    def decline_ratio(self) -> float:
        if self.total <= 0:
            return 0.5
        return self.declining / self.total


@dataclass
class SectorFlowItem:
    """Capital flow entry for an industry or concept board."""

    name: str
    change_pct: float  # 板块涨跌幅 (%)
    net_inflow: float  # 主力净流入（亿元）
    net_inflow_ratio: Optional[float] = None  # 主力净流入占比 (%)
    leader_stock: str = ""  # 领涨股


@dataclass
class SectorFlowSummary:
    """Dual-dimension (industry & concept) sector capital flow."""

    industry_inflow_top: List[SectorFlowItem] = field(default_factory=list)
    industry_outflow_top: List[SectorFlowItem] = field(default_factory=list)
    concept_inflow_top: List[SectorFlowItem] = field(default_factory=list)
    concept_outflow_top: List[SectorFlowItem] = field(default_factory=list)


@dataclass
class MarketSnapshot:
    """Complete A-share intraday snapshot collected every 30 minutes."""

    timestamp: datetime
    indices: List[IndexSnapshot]
    breadth: MarketBreadth
    sector_flows: SectorFlowSummary
    is_mock: bool = False
    warnings: List[str] = field(default_factory=list)

    def get_index(self, code: str) -> Optional[IndexSnapshot]:
        for idx in self.indices:
            if idx.code == code:
                return idx
        return None


@dataclass
class AlertResult:
    """Quantitative alert decision with cooldown metadata."""

    raw_level: AlertLevel  # 规则原始判定级别
    effective_level: AlertLevel  # 经 60 分钟防刷屏冷却处理后的最终级别
    is_suppressed_by_cooldown: bool = False
    cooldown_remaining_minutes: int = 0
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    summary_tag: str = "常规简报"


@dataclass
class AnalysisResult:
    """LLM or fallback rule-based commentary output."""

    content: str
    source: str  # "minimax" or "rule_fallback"
    model: str = ""
    latency_ms: int = 0
    error: Optional[str] = None
