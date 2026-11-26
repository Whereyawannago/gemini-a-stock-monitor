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
from .models import AlertLevel, AlertResult, AnalysisResult, MarketSnapshot, SectorFlowItem

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


class FeishuNotifier:
    """Sends interactive cards to a Feishu custom bot webhook."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(
        self,
        snapshot: MarketSnapshot,
        alert: AlertResult,
        analysis: AnalysisResult,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Build card payload and optionally POST to Feishu webhook."""
        payload = build_feishu_card_payload(
            snapshot=snapshot,
            alert=alert,
            analysis=analysis,
            secret=self.settings.feishu_webhook_secret,
        )

        if dry_run:
            logger.info("[DRY-RUN] 跳过飞书 Webhook 实际发送")
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

        logger.info("飞书交互卡片推送成功: %s", alert.summary_tag)
        return {"dry_run": False, "response": body, "payload": payload}
