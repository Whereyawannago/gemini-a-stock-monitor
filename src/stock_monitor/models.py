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
    activity_pct: Optional[float] = None  # 乐咕乐股市场活跃度 (%)

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


# ====================================================================== #
# 收盘复盘 (Daily close review)
# ====================================================================== #
@dataclass
class LimitStockItem:
    """A limit-up / limit-down / hot-rank stock entry."""

    code: str
    name: str
    change_pct: float = 0.0
    boards: int = 0  # 连板数 / 连续跌停
    industry: str = ""
    extra: str = ""  # 封板资金 / 人气排名等补充信息


@dataclass
class LimitPoolStats:
    """涨停板生态：涨停 / 炸板 / 跌停 / 连板高度 / 封板率 / 昨日涨停溢价."""

    limit_up_count: int = 0
    broken_count: int = 0  # 炸板
    limit_down_count: int = 0
    max_boards: int = 0  # 最高连板
    seal_rate: Optional[float] = None  # 封板率 = 涨停 / (涨停 + 炸板)
    prev_limit_up_avg_pct: Optional[float] = None  # 昨日涨停今日平均涨幅 (%)
    board_ladder: List[LimitStockItem] = field(default_factory=list)  # 连板梯队
    top_industries: List[str] = field(default_factory=list)  # 涨停集中行业, e.g. "半导体(8)"
    available: bool = False


@dataclass
class CapitalFlowOverview:
    """大盘资金 / 沪深港通 / 两融."""

    main_net_inflow: Optional[float] = None  # 两市主力净流入（亿元）
    main_net_inflow_source: str = ""  # 东方财富 / 同花顺行业汇总
    southbound_net_buy: Optional[float] = None  # 南向成交净买额（亿元）
    northbound_note: str = "北向资金自2024年8月起不再披露实时净买入"
    margin_balance: Optional[float] = None  # 沪市两融余额（亿元，T-1）
    margin_change: Optional[float] = None  # 两融余额较前一日变化（亿元）
    margin_date: str = ""


@dataclass
class SentimentScore:
    """0~100 market sentiment (heat) score with component breakdown."""

    score: float
    label: str
    components: List[str] = field(default_factory=list)


@dataclass
class DailyReview:
    """Complete post-close A-share review data."""

    trade_date: str
    timestamp: datetime
    market: MarketSnapshot  # 收盘时的指数 / 涨跌家数 / 双维度板块资金
    limit_pool: LimitPoolStats
    capital: CapitalFlowOverview
    hot_stocks: List[LimitStockItem]
    sentiment: SentimentScore
    is_mock: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class LhbItem:
    """Dragon-tiger list (龙虎榜) entry."""

    code: str
    name: str
    change_pct: float
    net_buy: float  # 龙虎榜净买额（亿元）
    reason: str = ""
    interpretation: str = ""


@dataclass
class LhbReview:
    """Post-close dragon-tiger list summary (published ~17:00)."""

    trade_date: str
    timestamp: datetime
    total_count: int
    institution_buy_count: int
    net_buy_top: List[LhbItem] = field(default_factory=list)
    net_sell_top: List[LhbItem] = field(default_factory=list)
    is_mock: bool = False
    warnings: List[str] = field(default_factory=list)


# ====================================================================== #
# 黄金监控 (Gold monitor)
# ====================================================================== #
class GoldAlertType(str, Enum):
    FAST_UP = "fast_up"  # 30分钟急涨
    FAST_DOWN = "fast_down"  # 30分钟急跌
    DAILY_UP = "daily_up"  # 日内涨幅超阈值
    DAILY_DOWN = "daily_down"  # 日内跌幅超阈值
    LEVEL_ABOVE = "level_above"  # 向上突破关键价位
    LEVEL_BELOW = "level_below"  # 向下跌破关键价位


@dataclass
class GoldQuote:
    """Single gold instrument quote."""

    key: str  # AU9999 / COMEX / LONDON
    name: str
    price: float
    unit: str  # 元/克 / 美元/盎司
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None  # 较昨收/昨结 (%)
    high: Optional[float] = None
    low: Optional[float] = None
    cny_per_gram: Optional[float] = None  # 折合人民币/克
    change_30m_pct: Optional[float] = None  # 近30分钟涨跌幅 (%)
    quote_time: str = ""


@dataclass
class GoldSnapshot:
    timestamp: datetime
    quotes: List[GoldQuote]
    usd_cny: Optional[float] = None
    premium_cny_per_gram: Optional[float] = None  # 上海金 - 伦敦金折算价
    is_mock: bool = False
    warnings: List[str] = field(default_factory=list)

    def get(self, key: str) -> Optional[GoldQuote]:
        for q in self.quotes:
            if q.key == key:
                return q
        return None


@dataclass
class GoldAlert:
    alert_type: GoldAlertType
    key: str
    message: str
    dedupe_key: str  # 冷却去重键


@dataclass
class PriceLevel:
    """User-defined key price level, e.g. AU9999 below 750."""

    key: str
    direction: str  # "above" / "below"
    price: float

    @property
    def id(self) -> str:
        return f"{self.key}:{self.direction}:{self.price:g}"
