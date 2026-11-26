"""Tests for AKShare compatibility layer, circuit breaker, and fallback parsers."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from stock_monitor.ak_compat import (
    EASTMONEY_GUARD,
    EastmoneyBlockedError,
    ak_call,
    resolve,
)
from stock_monitor.data_fetcher import parse_legu_activity, parse_sector_flow_df


def test_resolve_prefers_new_name_and_falls_back_to_legacy() -> None:
    new_mod = SimpleNamespace(index_zh_spot_em=lambda **kw: "new")
    assert ak_call("index_zh_spot_em", ak_module=new_mod) == "new"

    old_mod = SimpleNamespace(stock_zh_index_spot_em=lambda **kw: "old")
    assert ak_call("index_zh_spot_em", ak_module=old_mod) == "old"

    with pytest.raises(AttributeError):
        resolve("index_zh_spot_em", ak_module=SimpleNamespace())


def test_eastmoney_rate_limit_opens_circuit_breaker() -> None:
    EASTMONEY_GUARD.reset()

    class Push2RateLimitError(Exception):
        pass

    calls = {"count": 0}

    def flaky(**_kw):
        calls["count"] += 1
        raise Push2RateLimitError("当前 IP 被临时限流")

    fake_ak = SimpleNamespace(index_zh_spot_em=flaky)

    with pytest.raises(Push2RateLimitError):
        ak_call("index_zh_spot_em", ak_module=fake_ak)
    assert calls["count"] == 1
    assert EASTMONEY_GUARD.is_blocked()

    # Subsequent push2 call is short-circuited without invoking the API again
    with pytest.raises(EastmoneyBlockedError):
        ak_call("index_zh_spot_em", ak_module=fake_ak)
    assert calls["count"] == 1

    EASTMONEY_GUARD.reset()


def test_parse_legu_activity() -> None:
    df = pd.DataFrame(
        [
            {"item": "上涨", "value": 1683.0},
            {"item": "下跌", "value": 3375.0},
            {"item": "平盘", "value": 163.0},
            {"item": "涨停", "value": 70.0},
            {"item": "跌停", "value": 8.0},
            {"item": "活跃度", "value": "32.22%"},
        ]
    )
    breadth = parse_legu_activity(df)
    assert breadth is not None
    assert breadth.advancing == 1683
    assert breadth.declining == 3375
    assert breadth.unchanged == 163
    assert breadth.limit_up == 70
    assert breadth.limit_down == 8
    assert breadth.activity_pct == pytest.approx(32.22)


def test_parse_sector_flow_df_supports_ths_columns() -> None:
    ths_df = pd.DataFrame(
        [
            {"行业": "贵金属", "行业-涨跌幅": 4.22, "净额": 12.32, "领涨股": "招金黄金"},
            {"行业": "元件", "行业-涨跌幅": 3.94, "净额": -5.18, "领涨股": "金禄电子"},
        ]
    )
    items = parse_sector_flow_df(ths_df)
    assert len(items) == 2
    assert items[0].name == "贵金属"
    assert items[0].change_pct == pytest.approx(4.22)
    assert items[0].net_inflow == pytest.approx(12.32)
    assert items[0].leader_stock == "招金黄金"
    assert items[1].net_inflow == pytest.approx(-5.18)
