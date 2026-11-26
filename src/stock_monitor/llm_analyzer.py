"""MiniMax OpenAI-compatible LLM market commentary generator with fallback."""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, List, Optional

from openai import OpenAI

from .config import Settings
from .models import (
    AlertLevel,
    AlertResult,
    AnalysisResult,
    DailyReview,
    GoldAlert,
    GoldSnapshot,
    MarketSnapshot,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位资深的 A 股盘中量化交易员与市场策略分析师。
请严格依据用户提供的实时盘面数据（四大指数、5分钟VWAP、累计量能同比、近30分钟边际放量、全市场涨跌家数、行业与概念双维度资金流向、量化预警状态），输出**结构化 4 段式盘中解盘**。

要求：
1. 必须严格使用以下 4 个固定标题，每段 2~4 句，信息密度高、数据准确，总字数控制在 350~550 字：
   **一、大盘与量能格局**
   **二、双维度板块资金**
   **三、盘面强弱与风险信号**
   **四、后续盯盘关注点**
2. 不得编造未提供的数据；客观理性，不做具体个股买卖推荐。
3. 直接输出最终 4 段式正文，不要输出思考过程或开场白。"""

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK_RE = re.compile(r"<think>.*$", flags=re.DOTALL | re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> reasoning blocks (and any unclosed <think>)."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _UNCLOSED_THINK_RE.sub("", cleaned)
    return cleaned.strip()


def format_snapshot_for_prompt(
    snapshot: MarketSnapshot, alert: AlertResult
) -> str:
    """Serialize MarketSnapshot and AlertResult into a compact Chinese prompt."""
    lines = [
        f"监控时间：{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        f"量化状态：{alert.summary_tag}（原始判定={alert.raw_level.value}，最终级别={alert.effective_level.value}）",
    ]
    if alert.reasons:
        lines.append("量化依据：" + "；".join(alert.reasons))

    lines.append("\n【四大指数实时与量能指标】")
    for idx in snapshot.indices:
        vwap_str = (
            f"{idx.vwap:.2f}({idx.vwap_diff_pct:+.2f}%)"
            if idx.vwap is not None and idx.vwap_diff_pct is not None
            else "N/A"
        )
        yoy_str = (
            f"{idx.yoy_amount_ratio:.2f}x({idx.yoy_amount_diff_pct:+.1f}%)"
            if idx.yoy_amount_ratio is not None and idx.yoy_amount_diff_pct is not None
            else "N/A"
        )
        m30_str = (
            f"{idx.marginal_30m_ratio:.2f}x({idx.marginal_30m_diff_pct:+.1f}%)"
            if idx.marginal_30m_ratio is not None and idx.marginal_30m_diff_pct is not None
            else "N/A"
        )
        amt_yi = idx.today_amount / 1e8 if idx.today_amount else 0.0
        lines.append(
            f"- {idx.name}({idx.code}): 最新 {idx.latest_price:.2f} ({idx.change_pct:+.2f}%), "
            f"成交额 {amt_yi:.1f}亿, 5m VWAP={vwap_str}, "
            f"累计同比={yoy_str}, 近30m边际放量={m30_str}"
        )

    b = snapshot.breadth
    turnover_str = (
        f"{b.total_turnover / 1e8:.1f}亿元" if b.total_turnover else "N/A"
    )
    lines.append(
        f"\n【全市场广度】上涨 {b.advancing} 家（占比 {b.advance_ratio * 100:.1f}%），"
        f"下跌 {b.declining} 家，平盘 {b.unchanged} 家；"
        f"涨停 {b.limit_up} 家，跌停 {b.limit_down} 家；全市场成交额约 {turnover_str}。"
    )

    sf = snapshot.sector_flows
    lines.append("\n【行业板块资金流向】")
    lines.append(
        "净流入前排："
        + (
            "、".join(
                f"{s.name}({s.change_pct:+.2f}%, {s.net_inflow:+.2f}亿)"
                for s in sf.industry_inflow_top
            )
            or "暂无"
        )
    )
    lines.append(
        "净流出前排："
        + (
            "、".join(
                f"{s.name}({s.change_pct:+.2f}%, {s.net_inflow:+.2f}亿)"
                for s in sf.industry_outflow_top
            )
            or "暂无"
        )
    )

    lines.append("\n【概念板块资金流向】")
    lines.append(
        "净流入前排："
        + (
            "、".join(
                f"{s.name}({s.change_pct:+.2f}%, {s.net_inflow:+.2f}亿)"
                for s in sf.concept_inflow_top
            )
            or "暂无"
        )
    )
    lines.append(
        "净流出前排："
        + (
            "、".join(
                f"{s.name}({s.change_pct:+.2f}%, {s.net_inflow:+.2f}亿)"
                for s in sf.concept_outflow_top
            )
            or "暂无"
        )
    )
    return "\n".join(lines)


def build_rule_based_summary(
    snapshot: MarketSnapshot, alert: AlertResult
) -> str:
    """Generate a structured 4-section rule-based commentary when LLM is unavailable."""
    indices = snapshot.indices
    avg_pct = sum(i.change_pct for i in indices) / len(indices) if indices else 0.0
    strongest = max(indices, key=lambda x: x.change_pct) if indices else None
    weakest = min(indices, key=lambda x: x.change_pct) if indices else None

    above_vwap = [
        i.name for i in indices if i.vwap_diff_pct is not None and i.vwap_diff_pct > 0
    ]
    yoy_vals = [i.yoy_amount_ratio for i in indices if i.yoy_amount_ratio is not None]
    m30_vals = [i.marginal_30m_ratio for i in indices if i.marginal_30m_ratio is not None]
    avg_yoy = sum(yoy_vals) / len(yoy_vals) if yoy_vals else None
    avg_m30 = sum(m30_vals) / len(m30_vals) if m30_vals else None

    vol_desc = "量能数据待补齐"
    if avg_yoy is not None or avg_m30 is not None:
        parts = []
        if avg_yoy is not None:
            parts.append(f"累计量能同比昨日同时刻约 {avg_yoy:.2f}x（{(avg_yoy - 1) * 100:+.1f}%）")
        if avg_m30 is not None:
            parts.append(f"近30分钟边际量能约 {avg_m30:.2f}x（{(avg_m30 - 1) * 100:+.1f}%）")
        vol_desc = "，".join(parts)

    lead_desc = (
        f"领涨为{strongest.name}({strongest.change_pct:+.2f}%)，偏弱为{weakest.name}({weakest.change_pct:+.2f}%)"
        if strongest and weakest
        else "指数分化平稳"
    )
    vwap_desc = (
        f"{'、'.join(above_vwap)}运行在5分钟分时均价(VWAP)上方"
        if above_vwap
        else "主要指数均运行在5分钟分时均价(VWAP)下方"
    )

    sf = snapshot.sector_flows
    ind_in = "、".join(
        f"{s.name}({s.net_inflow:+.1f}亿)" for s in sf.industry_inflow_top[:3]
    ) or "暂无明显主线"
    ind_out = "、".join(
        f"{s.name}({s.net_inflow:+.1f}亿)" for s in sf.industry_outflow_top[:3]
    ) or "暂无"
    con_in = "、".join(
        f"{s.name}({s.net_inflow:+.1f}亿)" for s in sf.concept_inflow_top[:3]
    ) or "暂无明显主线"
    con_out = "、".join(
        f"{s.name}({s.net_inflow:+.1f}亿)" for s in sf.concept_outflow_top[:3]
    ) or "暂无"

    b = snapshot.breadth
    if alert.raw_level == AlertLevel.VOLUME_SURGE:
        focus = "重点观察放量主线板块的持续性与指数能否稳守5分钟VWAP上方，谨防短线冲高回落追高风险。"
    elif alert.raw_level == AlertLevel.VOLUME_PLUNGE:
        focus = "重点观察恐慌盘释放后能否出现缩量止跌与权重护盘信号，控制仓位节奏，避免盲目抄底。"
    else:
        focus = "重点观察后续半小时边际量能能否有效放大，以及资金是否向核心行业与概念主线集中。"

    return (
        f"**一、大盘与量能格局**\n"
        f"四大指数平均涨跌幅 {avg_pct:+.2f}%，{lead_desc}。{vwap_desc}；{vol_desc}。\n\n"
        f"**二、双维度板块资金**\n"
        f"行业维度主力净流入居前为{ind_in}，净流出居前为{ind_out}；"
        f"概念维度主力净流入居前为{con_in}，净流出居前为{con_out}。\n\n"
        f"**三、盘面强弱与风险信号**\n"
        f"全市场上涨 {b.advancing} 家、下跌 {b.declining} 家（上涨占比 {b.advance_ratio * 100:.1f}%），"
        f"涨停 {b.limit_up} 家、跌停 {b.limit_down} 家。当前量化状态为【{alert.summary_tag}】。\n\n"
        f"**四、后续盯盘关注点**\n"
        f"{focus}（注：本次为规则引擎自动降级摘要，仅供参考。）"
    )


class MiniMaxAnalyzer:
    """MiniMax OpenAI-compatible market analyst with automatic fallback."""

    def __init__(
        self,
        settings: Settings,
        client: Optional[OpenAI] = None,
    ) -> None:
        self.settings = settings
        self._client = client

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.settings.minimax_api_key,
                base_url=self.settings.minimax_base_url,
                timeout=self.settings.minimax_timeout,
            )
        return self._client

    def analyze_with_prompt(
        self,
        system_prompt: str,
        user_prompt: str,
        fallback_fn: Callable[[], str],
    ) -> AnalysisResult:
        """Call MiniMax with arbitrary system/user prompts, falling back on error."""
        start = time.monotonic()
        if not self.settings.minimax_api_key and self._client is None:
            logger.info("MINIMAX_API_KEY 未配置，自动降级为规则摘要")
            return AnalysisResult(
                content=fallback_fn(),
                source="rule_fallback",
                model="rule_engine",
                latency_ms=int((time.monotonic() - start) * 1000),
                error="MINIMAX_API_KEY not configured",
            )

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.settings.minimax_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=self.settings.minimax_max_tokens,
                temperature=self.settings.minimax_temperature,
                timeout=self.settings.minimax_timeout,
            )
            raw_content = ""
            if response.choices and response.choices[0].message:
                raw_content = response.choices[0].message.content or ""

            cleaned = strip_think_tags(raw_content)
            if not cleaned:
                raise ValueError(
                    "MiniMax returned empty content after stripping <think> tags"
                )

            return AnalysisResult(
                content=cleaned,
                source="minimax",
                model=self.settings.minimax_model,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("MiniMax 调用失败或超时，自动降级为规则摘要: %s", exc)
            return AnalysisResult(
                content=fallback_fn(),
                source="rule_fallback",
                model="rule_engine",
                latency_ms=int((time.monotonic() - start) * 1000),
                error=str(exc),
            )

    def analyze(
        self,
        snapshot: MarketSnapshot,
        alert: AlertResult,
    ) -> AnalysisResult:
        """Generate 4-section intraday commentary via MiniMax or rule fallback."""
        return self.analyze_with_prompt(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=format_snapshot_for_prompt(snapshot, alert),
            fallback_fn=lambda: build_rule_based_summary(snapshot, alert),
        )

    def analyze_review(self, review: DailyReview) -> AnalysisResult:
        """Generate 4-section post-close A-share review commentary."""
        from .daily_review import (
            REVIEW_SYSTEM_PROMPT,
            build_rule_review_summary,
            format_review_prompt,
        )

        return self.analyze_with_prompt(
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_prompt=format_review_prompt(review),
            fallback_fn=lambda: build_rule_review_summary(review),
        )

    def analyze_gold(
        self, snapshot: GoldSnapshot, alerts: List[GoldAlert]
    ) -> AnalysisResult:
        """Generate concise 3-section gold market commentary."""
        from .gold_monitor import (
            GOLD_SYSTEM_PROMPT,
            build_rule_gold_summary,
            format_gold_prompt,
        )

        return self.analyze_with_prompt(
            system_prompt=GOLD_SYSTEM_PROMPT,
            user_prompt=format_gold_prompt(snapshot, alerts),
            fallback_fn=lambda: build_rule_gold_summary(snapshot, alerts),
        )
