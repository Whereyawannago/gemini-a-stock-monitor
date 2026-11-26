"""Tests for MiniMax LLM analyzer, <think> tag filtering, and timeout fallback."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from stock_monitor.alert_engine import AlertEngine
from stock_monitor.config import Settings
from stock_monitor.llm_analyzer import (
    MiniMaxAnalyzer,
    build_rule_based_summary,
    strip_think_tags,
)
from stock_monitor.mock_data import build_mock_snapshot


def test_strip_think_tags_multiline_and_unclosed() -> None:
    raw = (
        "<think>\n先分析一下指数和量能...\n再看看板块。\n</think>\n\n"
        "**一、大盘与量能格局**\n指数放量上行。"
    )
    assert strip_think_tags(raw) == "**一、大盘与量能格局**\n指数放量上行。"

    unclosed = "正文前段<think>未闭合的思考内容"
    assert strip_think_tags(unclosed) == "正文前段"


def test_minimax_analyzer_calls_openai_compatible_api_and_strips_think(tmp_path) -> None:
    settings = Settings(
        minimax_api_key="test-key",
        minimax_base_url="https://api.minimaxi.com/v1",
        minimax_model="MiniMax-M3.1-Flash",
        minimax_max_tokens=2048,
        state_dir=tmp_path,
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "<think>推理过程</think>\n"
                        "**一、大盘与量能格局**\n放量走强。\n\n"
                        "**二、双维度板块资金**\n半导体领涨。\n\n"
                        "**三、盘面强弱与风险信号**\n普涨格局。\n\n"
                        "**四、后续盯盘关注点**\n关注量能持续性。"
                    )
                )
            )
        ]
    )

    snap = build_mock_snapshot("surge")
    alert = AlertEngine(settings).evaluate(snap, persist=False)
    analyzer = MiniMaxAnalyzer(settings, client=mock_client)
    result = analyzer.analyze(snap, alert)

    assert result.source == "minimax"
    assert result.model == "MiniMax-M3.1-Flash"
    assert "<think>" not in result.content
    assert "**一、大盘与量能格局**" in result.content

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "MiniMax-M3.1-Flash"
    assert call_kwargs["max_tokens"] == 2048


def test_minimax_analyzer_falls_back_on_timeout(tmp_path) -> None:
    settings = Settings(minimax_api_key="test-key", state_dir=tmp_path)
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("Read timed out")

    snap = build_mock_snapshot("plunge")
    alert = AlertEngine(settings).evaluate(snap, persist=False)
    analyzer = MiniMaxAnalyzer(settings, client=mock_client)
    result = analyzer.analyze(snap, alert)

    assert result.source == "rule_fallback"
    assert result.error is not None and "timed out" in result.error
    assert "**一、大盘与量能格局**" in result.content
    assert "**四、后续盯盘关注点**" in result.content


def test_minimax_analyzer_falls_back_when_key_missing(tmp_path) -> None:
    settings = Settings(minimax_api_key="", state_dir=tmp_path)
    snap = build_mock_snapshot("normal")
    alert = AlertEngine(settings).evaluate(snap, persist=False)
    result = MiniMaxAnalyzer(settings).analyze(snap, alert)

    assert result.source == "rule_fallback"
    assert result.content == build_rule_based_summary(snap, alert)
