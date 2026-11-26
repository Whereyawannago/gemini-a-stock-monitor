"""Feishu webhook interactive card builder and sender with HMAC-SHA256 signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .config import Settings
from .models import (
    AlertLevel,
    AlertResult,
    AnalysisResult,
    DailyReview,
    GoldAlert,
    GoldAlertType,
    GoldSnapshot,
    LhbReview,
    MarketSnapshot,
    SectorFlowItem,
)

logger = logging.getLogger(__name__)


def generate_feishu_sign(timestamp: int, secret: str) -> str:
    """Compute Feishu custom bot webhook HMAC-SHA256 signature."""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _format_sector_lines(items: List[SectorFlowItem], top_n: int = 5) -> str:
    if not items:
        return "暂无数据"
    parts = []
    for item in items[:top_n]:
        leader = f" · {item.leader_stock}" if item.leader_stock else ""
        parts.append(
            f"**{item.name}** {item.change_pct:+.2f}% / {item.net_inflow:+.2f}亿{leader}"
        )
    return "\n".join(parts)


def build_feishu_card_payload(
    snapshot: MarketSnapshot,
    alert: AlertResult,
    analysis: AnalysisResult,
    secret: str = "",
    timestamp: Optional[int] = None,
) -> Dict[str, Any]:
    """Build Feishu interactive card payload with color-coded header and optional sign."""
    if alert.effective_level == AlertLevel.VOLUME_SURGE:
        template = "red"
        title_prefix = "🔴 【放量上涨预警】"
    elif alert.effective_level == AlertLevel.VOLUME_PLUNGE:
        template = "green"
        title_prefix = "🟢 【放量下跌预警】"
    else:
        template = "blue"
        title_prefix = "🔵 【A股半小时盯盘简报】"

    mock_badge = " [MOCK]" if snapshot.is_mock else ""
    time_str = snapshot.timestamp.strftime("%m-%d %H:%M")
    title = f"{title_prefix} {time_str}{mock_badge}"

    elements: List[Dict[str, Any]] = []

    # 1. Alert / cooldown banner
    if alert.reasons:
        banner_lines = [f"**状态：{alert.summary_tag}**"]
        for r in alert.reasons:
            banner_lines.append(f"• {r}")
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(banner_lines)},
            }
        )
        elements.append({"tag": "hr"})

    # 2. Four benchmark indices overview
    index_lines = ["**📈 四大指数实时走势与量能（5m VWAP / 同比 / 近30m边际）**"]
    for idx in snapshot.indices:
        arrow = "🔺" if idx.change_pct > 0 else ("🔻" if idx.change_pct < 0 else "➖")
        vwap_str = (
            f"VWAP {idx.vwap:.2f}({idx.vwap_diff_pct:+.2f}%)"
            if idx.vwap is not None and idx.vwap_diff_pct is not None
            else "VWAP N/A"
        )
        yoy_str = (
            f"同比 {idx.yoy_amount_ratio:.2f}x({idx.yoy_amount_diff_pct:+.0f}%)"
            if idx.yoy_amount_ratio is not None and idx.yoy_amount_diff_pct is not None
            else "同比 N/A"
        )
        m30_str = (
            f"近30m {idx.marginal_30m_ratio:.2f}x({idx.marginal_30m_diff_pct:+.0f}%)"
            if idx.marginal_30m_ratio is not None and idx.marginal_30m_diff_pct is not None
            else "近30m N/A"
        )
        amt_yi = idx.today_amount / 1e8 if idx.today_amount else 0.0
        index_lines.append(
            f"{arrow} **{idx.name}** `{idx.latest_price:.2f}` (**{idx.change_pct:+.2f}%**) | "
            f"额 {amt_yi:.0f}亿 | {vwap_str} | {yoy_str} | {m30_str}"
        )

    elements.append(
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(index_lines)},
        }
    )

    # 3. Market breadth
    b = snapshot.breadth
    turnover_text = (
        f" | 全市场成交额 **{b.total_turnover / 1e8:.0f} 亿**"
        if b.total_turnover
        else ""
    )
    breadth_md = (
        f"**🧭 全市场涨跌家数**\n"
        f"上涨 **{b.advancing}**（{b.advance_ratio * 100:.1f}%） / "
        f"下跌 **{b.declining}** / 平盘 **{b.unchanged}** | "
        f"涨停 **{b.limit_up}** / 跌停 **{b.limit_down}**{turnover_text}"
    )
    elements.append({"tag": "hr"})
    elements.append(
        {"tag": "div", "text": {"tag": "lark_md", "content": breadth_md}}
    )

    # 4. Dual-dimension sector capital flows
    sf = snapshot.sector_flows
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**🏭 行业主力净流入 Top**\n"
                        + _format_sector_lines(sf.industry_inflow_top),
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**🏭 行业主力净流出 Top**\n"
                        + _format_sector_lines(sf.industry_outflow_top),
                    },
                },
            ],
        }
    )
    elements.append(
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**💡 概念主力净流入 Top**\n"
                        + _format_sector_lines(sf.concept_inflow_top),
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**💡 概念主力净流出 Top**\n"
                        + _format_sector_lines(sf.concept_outflow_top),
                    },
                },
            ],
        }
    )

    # 5. LLM 4-section commentary
    source_label = (
        f"MiniMax ({analysis.model})"
        if analysis.source == "minimax"
        else "规则引擎自动降级摘要"
    )
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🤖 盘中四段式解盘**（{source_label}）\n\n{analysis.content}",
            },
        }
    )

    # 6. Footer note
    footer_parts = [
        f"采集时间：{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        f"解盘耗时：{analysis.latency_ms}ms",
        "数据来源：AKShare（仅供参考，不构成投资建议）",
    ]
    if snapshot.warnings:
        footer_parts.append(f"采集告警：{len(snapshot.warnings)} 项")
    elements.append(
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": " | ".join(footer_parts)}],
        }
    )

    payload: Dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        },
    }

    if secret:
        ts = int(timestamp if timestamp is not None else time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = generate_feishu_sign(ts, secret)

    return payload


def _wrap_card(
    title: str,
    template: str,
    elements: List[Dict[str, Any]],
    secret: str = "",
    timestamp: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        },
    }
    if secret:
        ts = int(timestamp if timestamp is not None else time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = generate_feishu_sign(ts, secret)
    return payload


def build_review_card_payload(
    review: DailyReview,
    analysis: AnalysisResult,
    secret: str = "",
    timestamp: Optional[int] = None,
) -> Dict[str, Any]:
    """Build purple Feishu interactive card for daily post-close A-share review."""
    mock_badge = " [MOCK]" if review.is_mock else ""
    title = f"🟣 【A股收盘复盘】 {review.trade_date} · 情绪 {review.sentiment.score:.0f}/100 ({review.sentiment.label}){mock_badge}"

    elements: List[Dict[str, Any]] = []
    m = review.market
    b = m.breadth
    p = review.limit_pool
    c = review.capital
    s = review.sentiment

    # 1. Sentiment & market overview
    turnover_str = f"**{b.total_turnover / 1e8:.0f} 亿**" if b.total_turnover else "N/A"
    act_str = f" | 活跃度 **{b.activity_pct:.1f}%**" if b.activity_pct is not None else ""
    sent_lines = [
        f"**🔥 市场情绪与热度：{s.score:.0f} / 100（{s.label}）**",
        "• " + " | ".join(s.components),
        f"• 涨跌家数：上涨 **{b.advancing}**（{b.advance_ratio * 100:.1f}%） / 下跌 **{b.declining}** / 平盘 **{b.unchanged}**{act_str}",
        f"• 两市成交额：{turnover_str}",
    ]
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(sent_lines)}})
    elements.append({"tag": "hr"})

    # 2. Four benchmark indices
    idx_lines = ["**📊 四大指数收盘表现**"]
    for idx in m.indices:
        arrow = "🔺" if idx.change_pct > 0 else ("🔻" if idx.change_pct < 0 else "➖")
        yoy = f" | 量比 {idx.yoy_amount_ratio:.2f}x" if idx.yoy_amount_ratio is not None else ""
        amt_yi = idx.today_amount / 1e8 if idx.today_amount else 0.0
        idx_lines.append(
            f"{arrow} **{idx.name}** `{idx.latest_price:.2f}` (**{idx.change_pct:+.2f}%**) | 额 {amt_yi:.0f}亿{yoy}"
        )
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(idx_lines)}})
    elements.append({"tag": "hr"})

    # 3. Limit-up ecosystem & Capital overview
    seal_str = f"{p.seal_rate * 100:.0f}%" if p.seal_rate is not None else "N/A"
    prev_str = f"{p.prev_limit_up_avg_pct:+.2f}%" if p.prev_limit_up_avg_pct is not None else "N/A"
    ladder_str = (
        "、".join(f"{x.name}({x.boards}板)" for x in p.board_ladder[:5])
        if p.board_ladder
        else "暂无连板梯队"
    )
    ind_hot = "、".join(p.top_industries[:5]) or "暂无"

    main_flow = (
        f"**{c.main_net_inflow:+.2f} 亿**（{c.main_net_inflow_source}）"
        if c.main_net_inflow is not None
        else "N/A"
    )
    south_flow = f"**{c.southbound_net_buy:+.2f} 亿**" if c.southbound_net_buy is not None else "N/A"
    margin_str = (
        f"**{c.margin_balance:.0f} 亿**（{c.margin_date}，{c.margin_change:+.2f}亿）"
        if c.margin_balance is not None and c.margin_change is not None
        else (f"**{c.margin_balance:.0f} 亿**" if c.margin_balance is not None else "N/A")
    )

    elements.append(
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            "**🎯 涨停板生态**\n"
                            f"涨停 **{p.limit_up_count}** / 炸板 **{p.broken_count}** / 跌停 **{p.limit_down_count}**\n"
                            f"封板率 **{seal_str}** | 最高 **{p.max_boards}** 连板\n"
                            f"昨日涨停溢价 **{prev_str}**\n"
                            f"梯队：{ladder_str}\n"
                            f"涨停方向：{ind_hot}"
                        ),
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            "**💰 资金全景**\n"
                            f"两市主力净流入：{main_flow}\n"
                            f"南向成交净买额：{south_flow}\n"
                            f"沪市两融(T-1)：{margin_str}\n"
                            f"注：{c.northbound_note}"
                        ),
                    },
                },
            ],
        }
    )

    # 4. Dual-dimension sector flows
    sf = m.sector_flows
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**🏭 行业主力净流入 Top**\n"
                        + _format_sector_lines(sf.industry_inflow_top),
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**🏭 行业主力净流出 Top**\n"
                        + _format_sector_lines(sf.industry_outflow_top),
                    },
                },
            ],
        }
    )
    elements.append(
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**💡 概念主力净流入 Top**\n"
                        + _format_sector_lines(sf.concept_inflow_top),
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": "**💡 概念主力净流出 Top**\n"
                        + _format_sector_lines(sf.concept_outflow_top),
                    },
                },
            ],
        }
    )

    # 5. Hot rank stocks (optional)
    if review.hot_stocks:
        hot_text = " · ".join(
            f"{i + 1}.{x.name}({x.change_pct:+.1f}%)"
            for i, x in enumerate(review.hot_stocks[:8])
        )
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**🌟 个股人气榜 Top**\n{hot_text}"},
            }
        )

    # 6. LLM 4-section close review
    source_label = (
        f"MiniMax ({analysis.model})"
        if analysis.source == "minimax"
        else "规则引擎自动降级摘要"
    )
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🤖 收盘四段式复盘**（{source_label}）\n\n{analysis.content}",
            },
        }
    )

    footer = [
        f"复盘时间：{review.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        f"耗时：{analysis.latency_ms}ms",
        "数据来源：AKShare（仅供参考，不构成投资建议）",
    ]
    if review.warnings:
        footer.append(f"降级/告警：{len(review.warnings)} 项")
    elements.append(
        {"tag": "note", "elements": [{"tag": "plain_text", "content": " | ".join(footer)}]}
    )
    return _wrap_card(title, "purple", elements, secret=secret, timestamp=timestamp)


def build_lhb_card_payload(
    lhb: LhbReview,
    summary: str,
    secret: str = "",
    timestamp: Optional[int] = None,
) -> Dict[str, Any]:
    """Build indigo Feishu interactive card for post-close Dragon-Tiger List (龙虎榜)."""
    mock_badge = " [MOCK]" if lhb.is_mock else ""
    title = f"🐉 【A股龙虎榜复盘】 {lhb.trade_date} · 上榜 {lhb.total_count} 只{mock_badge}"

    elements: List[Dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**📋 龙虎榜概况**：上榜 **{lhb.total_count}** 只，"
                    f"机构席位买入 **{lhb.institution_buy_count}** 只\n{summary}"
                ),
            },
        },
        {"tag": "hr"},
    ]

    def _fmt_lhb(items) -> str:
        if not items:
            return "暂无数据"
        lines = []
        for x in items:
            tag = f" · {x.interpretation}" if x.interpretation else ""
            lines.append(f"**{x.name}**(`{x.code}`) {x.change_pct:+.2f}% / **{x.net_buy:+.2f}亿**{tag}")
        return "\n".join(lines)

    elements.append(
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {"tag": "lark_md", "content": "**🔺 龙虎榜净买入 Top**\n" + _fmt_lhb(lhb.net_buy_top)},
                },
                {
                    "is_short": True,
                    "text": {"tag": "lark_md", "content": "**🔻 龙虎榜净卖出 Top**\n" + _fmt_lhb(lhb.net_sell_top)},
                },
            ],
        }
    )

    footer = f"采集时间：{lhb.timestamp.strftime('%Y-%m-%d %H:%M:%S')} | 数据来源：AKShare 东方财富龙虎榜"
    elements.append(
        {"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]}
    )
    return _wrap_card(title, "indigo", elements, secret=secret, timestamp=timestamp)


def build_gold_card_payload(
    snapshot: GoldSnapshot,
    alerts: List[GoldAlert],
    analysis: AnalysisResult,
    secret: str = "",
    timestamp: Optional[int] = None,
) -> Dict[str, Any]:
    """Build Feishu interactive card for gold price briefs and volatility/level alerts."""
    up_types = {GoldAlertType.FAST_UP, GoldAlertType.DAILY_UP, GoldAlertType.LEVEL_ABOVE}
    down_types = {GoldAlertType.FAST_DOWN, GoldAlertType.DAILY_DOWN, GoldAlertType.LEVEL_BELOW}

    if alerts:
        has_up = any(a.alert_type in up_types for a in alerts)
        has_down = any(a.alert_type in down_types for a in alerts)
        if has_up and not has_down:
            template = "red"
            prefix = "🔴 【黄金异动/价位预警】"
        elif has_down and not has_up:
            template = "green"
            prefix = "🟢 【黄金异动/价位预警】"
        else:
            template = "orange"
            prefix = "🟠 【黄金双向异动预警】"
    else:
        template = "yellow"
        prefix = "🥇 【黄金价格简报】"

    mock_badge = " [MOCK]" if snapshot.is_mock else ""
    time_str = snapshot.timestamp.strftime("%m-%d %H:%M")
    title = f"{prefix} {time_str}{mock_badge}"

    elements: List[Dict[str, Any]] = []
    if alerts:
        alert_lines = ["**⚠️ 触发预警条件**"] + [f"• {a.message}" for a in alerts]
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(alert_lines)}})
        elements.append({"tag": "hr"})

    quote_lines = ["**💰 国内外黄金实时行情**"]
    for q in snapshot.quotes:
        arrow = "🔺" if (q.change_pct or 0) > 0 else ("🔻" if (q.change_pct or 0) < 0 else "➖")
        chg = f"**{q.change_pct:+.2f}%**" if q.change_pct is not None else "N/A"
        c30 = f" | 近30m {q.change_30m_pct:+.2f}%" if q.change_30m_pct is not None else ""
        cny = f" (≈{q.cny_per_gram:.2f}元/克)" if q.cny_per_gram and q.unit != "元/克" else ""
        quote_lines.append(f"{arrow} **{q.name}** `{q.price:.2f}` {q.unit} ({chg}){cny}{c30}")
    if snapshot.usd_cny or snapshot.premium_cny_per_gram is not None:
        meta = []
        if snapshot.usd_cny:
            meta.append(f"USD/CNY `{snapshot.usd_cny:.4f}`")
        if snapshot.premium_cny_per_gram is not None:
            meta.append(f"国内溢价 **{snapshot.premium_cny_per_gram:+.2f} 元/克**")
        quote_lines.append("• " + " | ".join(meta))

    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(quote_lines)}})
    elements.append({"tag": "hr"})

    source_label = (
        f"MiniMax ({analysis.model})"
        if analysis.source == "minimax"
        else "规则引擎自动摘要"
    )
    elements.append(
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🤖 黄金简评**（{source_label}）\n\n{analysis.content}",
            },
        }
    )

    footer = [
        f"采集时间：{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        "数据来源：AKShare（上海金交所 + 新浪外盘，仅供参考）",
    ]
    if snapshot.warnings:
        footer.append(f"告警：{len(snapshot.warnings)} 项")
    elements.append(
        {"tag": "note", "elements": [{"tag": "plain_text", "content": " | ".join(footer)}]}
    )
    return _wrap_card(title, template, elements, secret=secret, timestamp=timestamp)


class FeishuNotifier:
    """Sends interactive cards to a Feishu custom bot webhook."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_payload(
        self,
        payload: Dict[str, Any],
        label: str = "飞书卡片",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """POST an already-constructed Feishu card payload to the webhook."""
        if dry_run:
            logger.info("[DRY-RUN] 跳过飞书 Webhook 实际发送 (%s)", label)
            return {"dry_run": True, "payload": payload}

        if not self.settings.feishu_webhook_url:
            raise ValueError("FEISHU_WEBHOOK_URL 未配置，无法推送飞书消息")

        resp = requests.post(
            self.settings.feishu_webhook_url,
            json=payload,
            timeout=self.settings.feishu_timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        code = body.get("code", body.get("StatusCode", 0))
        if code != 0:
            raise RuntimeError(f"飞书推送返回错误: {body}")

        logger.info("飞书交互卡片推送成功: %s", label)
        return {"dry_run": False, "response": body, "payload": payload}

    def send(
        self,
        snapshot: MarketSnapshot,
        alert: AlertResult,
        analysis: AnalysisResult,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Build intraday card payload and optionally POST to Feishu webhook."""
        payload = build_feishu_card_payload(
            snapshot=snapshot,
            alert=alert,
            analysis=analysis,
            secret=self.settings.feishu_webhook_secret,
        )
        return self.send_payload(payload, label=alert.summary_tag, dry_run=dry_run)

    def send_review(
        self,
        review: DailyReview,
        analysis: AnalysisResult,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        payload = build_review_card_payload(
            review=review,
            analysis=analysis,
            secret=self.settings.feishu_webhook_secret,
        )
        return self.send_payload(payload, label=f"收盘复盘({review.trade_date})", dry_run=dry_run)

    def send_lhb(
        self,
        lhb: LhbReview,
        summary: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        payload = build_lhb_card_payload(
            lhb=lhb,
            summary=summary,
            secret=self.settings.feishu_webhook_secret,
        )
        return self.send_payload(payload, label=f"龙虎榜({lhb.trade_date})", dry_run=dry_run)

    def send_gold(
        self,
        snapshot: GoldSnapshot,
        alerts: List[GoldAlert],
        analysis: AnalysisResult,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        payload = build_gold_card_payload(
            snapshot=snapshot,
            alerts=alerts,
            analysis=analysis,
            secret=self.settings.feishu_webhook_secret,
        )
        label = f"黄金预警({len(alerts)}条)" if alerts else "黄金简报"
        return self.send_payload(payload, label=label, dry_run=dry_run)
