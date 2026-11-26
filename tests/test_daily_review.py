"""Tests for daily close review, sentiment scoring, LHB, and purple/indigo cards."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_monitor.config import Settings
from stock_monitor.daily_review import (
    build_lhb_summary,
    build_rule_review_summary,
    compute_sentiment,
    parse_hot_rank,
    parse_lhb,
    parse_limit_pools,
)
from stock_monitor.feishu_notifier import build_lhb_card_payload, build_review_card_payload
from stock_monitor.llm_analyzer import MiniMaxAnalyzer
from stock_monitor.main import main
from stock_monitor.mock_data import (
    build_mock_daily_review,
    build_mock_lhb_review,
    build_mock_snapshot,
)


def test_parse_limit_pools_and_sentiment() -> None:
    zt = pd.DataFrame(
        [
            {"代码": "000001", "名称": "龙头A", "涨跌幅": 10.0, "连板数": 5, "所属行业": "半导体", "封板资金": 2e8},
            {"代码": "000002", "名称": "跟风B", "涨跌幅": 10.0, "连板数": 2, "所属行业": "半导体", "封板资金": 1e8},
            {"代码": "000003", "名称": "首板C", "涨跌幅": 10.0, "连板数": 1, "所属行业": "通信", "封板资金": 0.5e8},
        ]
    )
    zb = pd.DataFrame([{"代码": "000004", "名称": "炸板D"}])
    dt = pd.DataFrame([{"代码": "000005", "名称": "跌停E"}])
    prev = pd.DataFrame([{"涨跌幅": 4.0}, {"涨跌幅": 2.0}])

    stats = parse_limit_pools(zt, zb, dt, prev)
    assert stats.available is True
    assert stats.limit_up_count == 3
    assert stats.broken_count == 1
    assert stats.limit_down_count == 1
    assert stats.max_boards == 5
    assert stats.seal_rate == pytest.approx(0.75)
    assert stats.prev_limit_up_avg_pct == pytest.approx(3.0)
    assert len(stats.board_ladder) == 2
    assert stats.board_ladder[0].name == "龙头A"
    assert stats.top_industries[0] == "半导体(2)"

    market = build_mock_snapshot("surge")
    sent = compute_sentiment(market, stats)
    assert 65.0 <= sent.score <= 100.0
    assert sent.label in {"偏热", "极度亢奋"}


def test_parse_hot_rank_and_lhb() -> None:
    hot_df = pd.DataFrame(
        [
            {"当前排名": 1, "代码": "SZ300059", "股票名称": "东方财富", "涨跌幅": 3.5},
            {"当前排名": 2, "代码": "SH688981", "股票名称": "中芯国际", "涨跌幅": -1.2},
        ]
    )
    hot = parse_hot_rank(hot_df)
    assert len(hot) == 2
    assert hot[0].code == "300059"
    assert hot[0].name == "东方财富"

    lhb_df = pd.DataFrame(
        [
            {"代码": "300308", "名称": "中际旭创", "涨跌幅": 11.4, "龙虎榜净买额": 4.5e8, "解读": "3家机构买入", "上榜原因": "涨幅偏离"},
            {"代码": "300308", "名称": "中际旭创", "涨跌幅": 11.4, "龙虎榜净买额": 4.5e8, "解读": "3家机构买入", "上榜原因": "重复记录"},
            {"代码": "000530", "名称": "冰山冷热", "涨跌幅": -6.0, "龙虎榜净买额": -1.2e8, "解读": "普通席位卖出", "上榜原因": "换手率"},
        ]
    )
    total, inst, buy, sell = parse_lhb(lhb_df)
    assert total == 2  # deduplicated by code
    assert inst == 1
    assert buy[0].name == "中际旭创" and buy[0].net_buy == pytest.approx(4.5)
    assert sell[0].name == "冰山冷热" and sell[0].net_buy == pytest.approx(-1.2)


def test_review_and_lhb_cards_and_fallback(tmp_path) -> None:
    settings = Settings(minimax_api_key="", state_dir=tmp_path)
    review = build_mock_daily_review("normal")
    analysis = MiniMaxAnalyzer(settings).analyze_review(review)
    assert analysis.source == "rule_fallback"
    assert analysis.content == build_rule_review_summary(review)
    assert "**一、今日盘面总结**" in analysis.content
    assert "**四、明日展望与策略**" in analysis.content

    card = build_review_card_payload(review, analysis, secret="sec", timestamp=1732579200)
    assert card["card"]["header"]["template"] == "purple"
    assert "sign" in card

    lhb = build_mock_lhb_review()
    lhb_card = build_lhb_card_payload(lhb, build_lhb_summary(lhb))
    assert lhb_card["card"]["header"]["template"] == "indigo"


def test_cli_review_and_lhb_mock_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "")

    assert main(["--once", "--job", "review", "--mock", "--dry-run", "--print-json"]) == 0
    out = capsys.readouterr().out
    assert '"template": "purple"' in out
    assert "今日盘面总结" in out

    assert main(["--once", "--job", "lhb", "--mock", "--dry-run", "--print-json"]) == 0
    out = capsys.readouterr().out
    assert '"template": "indigo"' in out
    assert "龙虎榜" in out
