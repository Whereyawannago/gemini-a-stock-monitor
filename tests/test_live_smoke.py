"""Optional live AKShare smoke tests (enable with RUN_LIVE_TESTS=1)."""

from __future__ import annotations

import os

import pytest

from stock_monitor.ak_compat import resolve
from stock_monitor.config import Settings
from stock_monitor.pipeline import MonitorPipeline

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="Set RUN_LIVE_TESTS=1 to run live AKShare smoke tests",
)


def test_akshare_canonical_interfaces_exist() -> None:
    """Ensure every AKShare API name used by the project resolves cleanly."""
    required = [
        "index_zh_spot_em",
        "index_zh_spot_sina",
        "index_zh_hist_min_em",
        "stock_zh_a_hist_min_sina",
        "stock_fund_flow_sector_rank_em",
        "stock_fund_flow_industry_ths",
        "stock_fund_flow_concept_ths",
        "stock_market_activity_legu",
        "calendar_trade_date_hist_sina",
        "stock_zt_pool_em",
        "stock_zt_pool_zbgc_em",
        "stock_zt_pool_dtgc_em",
        "stock_lhb_detail_em",
        "stock_margin_sse",
        "stock_hsgt_fund_flow_summary_em",
        "spot_realtime_sge",
        "spot_hist_sge",
        "futures_foreign_realtime_sina",
        "fx_spot_quote",
    ]
    for name in required:
        assert callable(resolve(name))


def test_live_gold_brief_dry_run(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path, minimax_api_key="")
    res = MonitorPipeline(settings).run_gold_check(mode="brief", mock=False, dry_run=True)
    assert res.sent is True
    assert len(res.snapshot.quotes) >= 2
    assert res.snapshot.get("AU9999") is not None


def test_live_review_dry_run(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path, minimax_api_key="")
    res = MonitorPipeline(settings).run_daily_review(mock=False, dry_run=True)
    assert len(res.review.market.indices) == 4
    assert res.review.market.indices[0].latest_price > 0
    assert res.review.market.breadth.total > 0
