"""Post-close daily A-share review: market recap, capital flows, sentiment/heat, LHB."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from .ak_compat import ak_call
from .config import Settings
from .data_fetcher import AKShareDataFetcher, _retry, _to_float, pick_col
from .models import (
    CapitalFlowOverview,
    DailyReview,
    LhbItem,
    LhbReview,
    LimitPoolStats,
    LimitStockItem,
    MarketBreadth,
    MarketSnapshot,
    SentimentScore,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# Pure parsing / scoring helpers (unit-tested offline)
# ---------------------------------------------------------------------- #
def parse_limit_pools(
    zt_df: Optional[pd.DataFrame],
    broken_df: Optional[pd.DataFrame],
    dt_df: Optional[pd.DataFrame],
    prev_zt_df: Optional[pd.DataFrame] = None,
    ladder_min_boards: int = 2,
    ladder_size: int = 8,
) -> LimitPoolStats:
    """Build limit-up ecosystem stats from Eastmoney limit pools."""
    stats = LimitPoolStats()
    has_any = False

    if zt_df is not None and not zt_df.empty:
        has_any = True
        stats.limit_up_count = len(zt_df)
        boards_col = pick_col(zt_df, ["连板数"])
        ind_col = pick_col(zt_df, ["所属行业"])
        pct_col = pick_col(zt_df, ["涨跌幅"])
        seal_col = pick_col(zt_df, ["封板资金"])
        if boards_col:
            boards = pd.to_numeric(zt_df[boards_col], errors="coerce").fillna(1).astype(int)
            stats.max_boards = int(boards.max()) if len(boards) else 0
            ladder_df = zt_df.assign(_boards=boards)
            ladder_df = ladder_df[ladder_df["_boards"] >= ladder_min_boards]
            ladder_df = ladder_df.sort_values("_boards", ascending=False).head(ladder_size)
            for _, row in ladder_df.iterrows():
                seal = _to_float(row.get(seal_col)) if seal_col else 0.0
                stats.board_ladder.append(
                    LimitStockItem(
                        code=str(row.get("代码", "")),
                        name=str(row.get("名称", "")),
                        change_pct=round(_to_float(row.get(pct_col)), 2) if pct_col else 0.0,
                        boards=int(row["_boards"]),
                        industry=str(row.get(ind_col, "")) if ind_col else "",
                        extra=f"封单{seal / 1e8:.2f}亿" if seal > 0 else "",
                    )
                )
        if ind_col:
            counter = Counter(str(x) for x in zt_df[ind_col].dropna() if str(x).strip())
            stats.top_industries = [f"{k}({v})" for k, v in counter.most_common(5)]

    if broken_df is not None and not broken_df.empty:
        has_any = True
        stats.broken_count = len(broken_df)

    if dt_df is not None and not dt_df.empty:
        has_any = True
        stats.limit_down_count = len(dt_df)

    denom = stats.limit_up_count + stats.broken_count
    if denom > 0:
        stats.seal_rate = round(stats.limit_up_count / denom, 4)

    if prev_zt_df is not None and not prev_zt_df.empty:
        pct_col = pick_col(prev_zt_df, ["涨跌幅"])
        if pct_col:
            pct = pd.to_numeric(prev_zt_df[pct_col], errors="coerce").dropna()
            if len(pct):
                stats.prev_limit_up_avg_pct = round(float(pct.mean()), 2)

    stats.available = has_any
    return stats


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_sentiment(market: MarketSnapshot, pool: LimitPoolStats) -> SentimentScore:
    """Compute a 0~100 sentiment (heat) score; missing factors are re-weighted."""
    factors: List[Tuple[str, float, float]] = []  # (desc, normalized 0~1, weight)
    breadth: MarketBreadth = market.breadth

    if breadth.total > 0:
        factors.append((f"上涨占比 {breadth.advance_ratio * 100:.0f}%", breadth.advance_ratio, 30))

    if market.indices:
        avg_pct = sum(i.change_pct for i in market.indices) / len(market.indices)
        factors.append((f"指数均涨幅 {avg_pct:+.2f}%", _clamp01((avg_pct + 2.0) / 4.0), 15))

    if pool.available:
        zt, dt = pool.limit_up_count, pool.limit_down_count
        if zt + dt > 0:
            factors.append((f"涨停/跌停 {zt}/{dt}", zt / (zt + dt), 15))
        if pool.seal_rate is not None:
            factors.append((f"封板率 {pool.seal_rate * 100:.0f}%", pool.seal_rate, 15))
        if pool.max_boards > 0:
            factors.append((f"最高 {pool.max_boards} 连板", _clamp01(pool.max_boards / 7.0), 10))
        if pool.prev_limit_up_avg_pct is not None:
            p = pool.prev_limit_up_avg_pct
            factors.append((f"昨日涨停溢价 {p:+.2f}%", _clamp01((p + 2.0) / 6.0), 15))

    if not factors:
        return SentimentScore(score=50.0, label="数据不足", components=["情绪数据缺失，默认中性"])

    total_w = sum(w for _, _, w in factors)
    score = round(sum(v * w for _, v, w in factors) / total_w * 100.0, 1)
    if score >= 80:
        label = "极度亢奋"
    elif score >= 65:
        label = "偏热"
    elif score >= 45:
        label = "中性"
    elif score >= 30:
        label = "偏冷"
    else:
        label = "冰点"
    return SentimentScore(score=score, label=label, components=[d for d, _, _ in factors])


def parse_hot_rank(df: Optional[pd.DataFrame], top_n: int = 10) -> List[LimitStockItem]:
    if df is None or df.empty:
        return []
    code_col = pick_col(df, ["代码", "股票代码"])
    name_col = pick_col(df, ["股票名称", "名称"])
    pct_col = pick_col(df, ["涨跌幅"])
    rank_col = pick_col(df, ["当前排名", "排名"])
    items = []
    for _, row in df.head(top_n).iterrows():
        code = str(row.get(code_col, "")) if code_col else ""
        items.append(
            LimitStockItem(
                code=code[-6:] if code else "",
                name=str(row.get(name_col, "")) if name_col else "",
                change_pct=round(_to_float(row.get(pct_col)), 2) if pct_col else 0.0,
                extra=f"人气第{int(_to_float(row.get(rank_col)))}" if rank_col else "",
            )
        )
    return items


def parse_lhb(df: Optional[pd.DataFrame], top_n: int = 5) -> Tuple[int, int, List[LhbItem], List[LhbItem]]:
    """Return (stock_count, institution_buy_count, net_buy_top, net_sell_top)."""
    if df is None or df.empty:
        return 0, 0, [], []
    df = df.drop_duplicates(subset=["代码"], keep="first") if "代码" in df.columns else df
    net_col = pick_col(df, ["龙虎榜净买额", "净买额"])
    interp_col = pick_col(df, ["解读"])
    items: List[LhbItem] = []
    inst_count = 0
    for _, row in df.iterrows():
        interp = str(row.get(interp_col, "")) if interp_col else ""
        if "机构买入" in interp:
            inst_count += 1
        items.append(
            LhbItem(
                code=str(row.get("代码", "")),
                name=str(row.get("名称", "")),
                change_pct=round(_to_float(row.get("涨跌幅")), 2),
                net_buy=round(_to_float(row.get(net_col)) / 1e8, 2) if net_col else 0.0,
                reason=str(row.get("上榜原因", "")),
                interpretation=interp,
            )
        )
    buy_top = [i for i in sorted(items, key=lambda x: x.net_buy, reverse=True) if i.net_buy > 0][:top_n]
    sell_top = [i for i in sorted(items, key=lambda x: x.net_buy) if i.net_buy < 0][:top_n]
    return len(items), inst_count, buy_top, sell_top


# ---------------------------------------------------------------------- #
# Fetchers
# ---------------------------------------------------------------------- #
class DailyReviewFetcher:
    """Collects post-close review data; every block degrades independently."""

    def __init__(self, settings: Settings, market_fetcher: Optional[AKShareDataFetcher] = None) -> None:
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)
        self.market_fetcher = market_fetcher or AKShareDataFetcher(settings)

    def _safe(self, label: str, func, warnings: List[str], retries: int = 2):
        try:
            return _retry(func, retries=retries, label=label)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{label} 获取失败: {str(exc)[:80]}")
            return None

    def fetch_review(self) -> DailyReview:
        now = datetime.now(self.tz)
        date_str = now.strftime("%Y%m%d")
        market = self.market_fetcher.fetch_snapshot()
        warnings = list(market.warnings)

        zt = self._safe("涨停池", lambda: ak_call("stock_zt_pool_em", date=date_str), warnings)
        zb = self._safe("炸板池", lambda: ak_call("stock_zt_pool_zbgc_em", date=date_str), warnings)
        dt = self._safe("跌停池", lambda: ak_call("stock_zt_pool_dtgc_em", date=date_str), warnings)
        prev_zt = self._safe(
            "昨日涨停表现", lambda: ak_call("stock_zt_pool_previous_em", date=date_str), warnings
        )
        pool = parse_limit_pools(zt, zb, dt, prev_zt)

        capital = self._fetch_capital(now, warnings)
        hot_df = self._safe("个股人气榜", lambda: ak_call("stock_hot_rank_em"), warnings, retries=1)
        hot = parse_hot_rank(hot_df)

        return DailyReview(
            trade_date=now.strftime("%Y-%m-%d"),
            timestamp=now,
            market=market,
            limit_pool=pool,
            capital=capital,
            hot_stocks=hot,
            sentiment=compute_sentiment(market, pool),
            warnings=warnings,
        )

    def _fetch_capital(self, now: datetime, warnings: List[str]) -> CapitalFlowOverview:
        cap = CapitalFlowOverview()

        # 1) 两市主力净流入：东方财富大盘资金流 -> 同花顺行业资金净额汇总
        mf = self._safe("大盘资金流", lambda: ak_call("stock_fund_flow_market_em"), warnings, retries=1)
        if mf is not None and not mf.empty:
            col = pick_col(mf, ["主力净流入-净额"])
            if col:
                cap.main_net_inflow = round(_to_float(mf[col].iloc[-1]) / 1e8, 2)
                cap.main_net_inflow_source = "东方财富"
        if cap.main_net_inflow is None:
            ind = self._safe(
                "同花顺行业资金流", lambda: ak_call("stock_fund_flow_industry_ths", symbol="即时"), warnings
            )
            if ind is not None and not ind.empty and "净额" in ind.columns:
                cap.main_net_inflow = round(float(pd.to_numeric(ind["净额"], errors="coerce").sum()), 2)
                cap.main_net_inflow_source = "同花顺行业汇总"

        # 2) 南向资金（北向自 2024-08 起不披露实时净买入）
        hsgt = self._safe("沪深港通资金", lambda: ak_call("stock_hsgt_fund_flow_summary_em"), warnings)
        if hsgt is not None and not hsgt.empty and "资金方向" in hsgt.columns:
            south = hsgt[hsgt["资金方向"] == "南向"]
            if not south.empty and "成交净买额" in south.columns:
                cap.southbound_net_buy = round(
                    float(pd.to_numeric(south["成交净买额"], errors="coerce").sum()), 2
                )

        # 3) 沪市两融余额（T-1）
        start = (now - timedelta(days=14)).strftime("%Y%m%d")
        margin = self._safe(
            "沪市两融",
            lambda: ak_call("stock_margin_sse", start_date=start, end_date=now.strftime("%Y%m%d")),
            warnings,
        )
        if margin is not None and not margin.empty:
            date_col = pick_col(margin, ["信用交易日期"])
            bal_col = pick_col(margin, ["融资融券余额"])
            if date_col and bal_col:
                m = margin.sort_values(date_col, ascending=False).reset_index(drop=True)
                cap.margin_balance = round(_to_float(m[bal_col].iloc[0]) / 1e8, 2)
                cap.margin_date = str(m[date_col].iloc[0])
                if len(m) > 1:
                    cap.margin_change = round(
                        (_to_float(m[bal_col].iloc[0]) - _to_float(m[bal_col].iloc[1])) / 1e8, 2
                    )
        return cap

    def fetch_lhb(self) -> LhbReview:
        now = datetime.now(self.tz)
        date_str = now.strftime("%Y%m%d")
        warnings: List[str] = []
        df = self._safe(
            "龙虎榜",
            lambda: ak_call("stock_lhb_detail_em", start_date=date_str, end_date=date_str),
            warnings,
        )
        count, inst, buy, sell = parse_lhb(df)
        if count == 0:
            warnings.append("今日龙虎榜暂未发布或为空（交易所通常 17:00 后披露）")
        return LhbReview(
            trade_date=now.strftime("%Y-%m-%d"),
            timestamp=now,
            total_count=count,
            institution_buy_count=inst,
            net_buy_top=buy,
            net_sell_top=sell,
            warnings=warnings,
        )


# ---------------------------------------------------------------------- #
# Prompt & rule-based fallback
# ---------------------------------------------------------------------- #
REVIEW_SYSTEM_PROMPT = """你是一位资深的 A 股策略分析师，负责撰写每日收盘复盘。
请严格依据用户提供的收盘数据（四大指数、成交额与量能同比、涨跌家数、涨停板生态、两市主力资金、行业与概念资金流向、南向资金、两融、个股人气、情绪评分），输出**结构化 4 段式收盘复盘**。

要求：
1. 必须严格使用以下 4 个固定标题，每段 2~4 句，数据准确、观点鲜明，总字数控制在 400~600 字：
   **一、今日盘面总结**
   **二、资金流向与主线**
   **三、市场情绪与热度**
   **四、明日展望与策略**
2. 不得编造未提供的数据；客观理性，不做具体个股买卖推荐。
3. 直接输出最终 4 段式正文，不要输出思考过程或开场白。"""


def _fmt_opt(value: Optional[float], fmt: str, suffix: str = "", na: str = "N/A") -> str:
    return f"{value:{fmt}}{suffix}" if value is not None else na


def format_review_prompt(review: DailyReview) -> str:
    m = review.market
    lines = [f"复盘日期：{review.trade_date}", "\n【四大指数收盘】"]
    for idx in m.indices:
        lines.append(
            f"- {idx.name}: 收 {idx.latest_price:.2f} ({idx.change_pct:+.2f}%), "
            f"成交额 {idx.today_amount / 1e8:.0f}亿, 全天量能同比 "
            f"{_fmt_opt(idx.yoy_amount_ratio, '.2f', 'x')}, 收盘相对VWAP {_fmt_opt(idx.vwap_diff_pct, '+.2f', '%')}"
        )
    b = m.breadth
    lines.append(
        f"\n【涨跌家数】上涨 {b.advancing}，下跌 {b.declining}，平盘 {b.unchanged}（上涨占比 {b.advance_ratio * 100:.1f}%）；"
        f"两市成交额 {_fmt_opt(b.total_turnover / 1e8 if b.total_turnover else None, '.0f', '亿')}"
    )
    p = review.limit_pool
    lines.append(
        f"\n【涨停板生态】涨停 {p.limit_up_count}，炸板 {p.broken_count}，跌停 {p.limit_down_count}，"
        f"封板率 {_fmt_opt(p.seal_rate * 100 if p.seal_rate is not None else None, '.0f', '%')}，"
        f"最高 {p.max_boards} 连板，昨日涨停今日平均 {_fmt_opt(p.prev_limit_up_avg_pct, '+.2f', '%')}；"
        f"涨停集中行业：{'、'.join(p.top_industries) or '暂无'}"
    )
    if p.board_ladder:
        lines.append(
            "连板梯队：" + "、".join(f"{s.name}({s.boards}板,{s.industry})" for s in p.board_ladder)
        )
    c = review.capital
    lines.append(
        f"\n【资金】两市主力净流入 {_fmt_opt(c.main_net_inflow, '+.2f', '亿')}（{c.main_net_inflow_source or '无'}口径）；"
        f"南向成交净买额 {_fmt_opt(c.southbound_net_buy, '+.2f', '亿')}；{c.northbound_note}；"
        f"沪市两融余额 {_fmt_opt(c.margin_balance, '.0f', '亿')}（{c.margin_date}，较前日 {_fmt_opt(c.margin_change, '+.2f', '亿')}）"
    )
    sf = m.sector_flows
    fmt_items = lambda items: "、".join(f"{s.name}({s.change_pct:+.2f}%, {s.net_inflow:+.2f}亿)" for s in items) or "暂无"  # noqa: E731
    lines.append(f"\n【行业资金】净流入：{fmt_items(sf.industry_inflow_top)}；净流出：{fmt_items(sf.industry_outflow_top)}")
    lines.append(f"【概念资金】净流入：{fmt_items(sf.concept_inflow_top)}；净流出：{fmt_items(sf.concept_outflow_top)}")
    if review.hot_stocks:
        lines.append(
            "\n【个股人气榜】" + "、".join(f"{s.name}({s.change_pct:+.2f}%)" for s in review.hot_stocks[:10])
        )
    s = review.sentiment
    lines.append(f"\n【情绪评分】{s.score:.0f}/100（{s.label}）：{'；'.join(s.components)}")
    return "\n".join(lines)


def build_rule_review_summary(review: DailyReview) -> str:
    m = review.market
    idx = m.indices
    avg = sum(i.change_pct for i in idx) / len(idx) if idx else 0.0
    best = max(idx, key=lambda x: x.change_pct) if idx else None
    worst = min(idx, key=lambda x: x.change_pct) if idx else None
    yoy = [i.yoy_amount_ratio for i in idx if i.yoy_amount_ratio is not None]
    avg_yoy = sum(yoy) / len(yoy) if yoy else None
    b = m.breadth
    turnover = f"两市成交额约 {b.total_turnover / 1e8:.0f} 亿，" if b.total_turnover else ""
    vol = (
        f"量能较昨日{'放大' if avg_yoy >= 1 else '萎缩'} {abs(avg_yoy - 1) * 100:.0f}%。"
        if avg_yoy is not None
        else ""
    )
    sf = m.sector_flows
    names = lambda items: "、".join(s.name for s in items[:3]) or "暂无"  # noqa: E731
    p = review.limit_pool
    c = review.capital
    s = review.sentiment

    if s.score >= 65:
        outlook = "情绪处于偏热区间，明日关注高位连板股分歧与主线板块能否持续放量，避免追高。"
    elif s.score >= 45:
        outlook = "情绪中性，明日关注量能能否持续放大以及资金是否向主线板块集中，可围绕主线低吸。"
    else:
        outlook = "情绪偏冷，明日关注缩量企稳信号与权重护盘力度，控制仓位、耐心等待情绪修复。"

    return (
        "**一、今日盘面总结**\n"
        f"四大指数平均涨跌 {avg:+.2f}%"
        + (f"，{best.name}最强({best.change_pct:+.2f}%)、{worst.name}最弱({worst.change_pct:+.2f}%)" if best and worst else "")
        + f"。{turnover}{vol}全市场上涨 {b.advancing} 家、下跌 {b.declining} 家。\n\n"
        "**二、资金流向与主线**\n"
        f"两市主力净流入 {_fmt_opt(c.main_net_inflow, '+.1f', ' 亿')}；行业资金流入居前为{names(sf.industry_inflow_top)}，"
        f"流出居前为{names(sf.industry_outflow_top)}；概念资金流入居前为{names(sf.concept_inflow_top)}。"
        f"南向成交净买额 {_fmt_opt(c.southbound_net_buy, '+.1f', ' 亿')}。\n\n"
        "**三、市场情绪与热度**\n"
        f"情绪评分 {s.score:.0f}/100（{s.label}）。涨停 {p.limit_up_count} 家、炸板 {p.broken_count} 家、跌停 {p.limit_down_count} 家，"
        f"封板率 {_fmt_opt(p.seal_rate * 100 if p.seal_rate is not None else None, '.0f', '%')}，最高 {p.max_boards} 连板；"
        f"涨停集中在{'、'.join(p.top_industries[:3]) or '暂无明显方向'}。\n\n"
        "**四、明日展望与策略**\n"
        f"{outlook}（注：本次为规则引擎自动生成复盘，仅供参考。）"
    )


def build_lhb_summary(lhb: LhbReview) -> str:
    if lhb.total_count == 0:
        return "今日龙虎榜暂无数据（交易所通常于 17:00 后披露），请稍后手动执行 `--job lhb` 补发。"
    buy = "、".join(f"{i.name}({i.net_buy:+.2f}亿)" for i in lhb.net_buy_top[:3]) or "暂无"
    sell = "、".join(f"{i.name}({i.net_buy:+.2f}亿)" for i in lhb.net_sell_top[:3]) or "暂无"
    return (
        f"今日共 {lhb.total_count} 只个股登上龙虎榜，其中 {lhb.institution_buy_count} 只获机构席位买入。"
        f"净买入居前：{buy}；净卖出居前：{sell}。（仅供参考，不构成投资建议）"
    )
