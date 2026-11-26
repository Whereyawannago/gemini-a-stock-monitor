"""High-fidelity mock A-share market snapshot generator for --mock testing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from .models import (
    CapitalFlowOverview,
    DailyReview,
    GoldQuote,
    GoldSnapshot,
    IndexSnapshot,
    LhbItem,
    LhbReview,
    LimitPoolStats,
    LimitStockItem,
    MarketBreadth,
    MarketSnapshot,
    SectorFlowItem,
    SectorFlowSummary,
)

MockScenario = Literal["normal", "surge", "plunge"]


def build_mock_snapshot(
    scenario: MockScenario = "normal",
    timezone: str = "Asia/Shanghai",
    now: datetime | None = None,
) -> MarketSnapshot:
    """Generate realistic mock snapshots for normal, volume_surge, or volume_plunge."""
    tz = ZoneInfo(timezone)
    ts = now if now is not None else datetime.now(tz)

    if scenario == "surge":
        indices = [
            IndexSnapshot(
                code="000001",
                name="上证指数",
                symbol="sh000001",
                latest_price=3412.65,
                prev_close=3368.40,
                change_amount=44.25,
                change_pct=1.31,
                today_amount=4820e8,
                today_volume=4.35e8,
                vwap=3391.20,
                vwap_diff_pct=0.63,
                yoy_amount_ratio=1.34,
                yoy_amount_diff_pct=34.0,
                marginal_30m_amount=1120e8,
                marginal_30m_ratio=1.52,
                marginal_30m_diff_pct=52.0,
            ),
            IndexSnapshot(
                code="399001",
                name="深证成指",
                symbol="sz399001",
                latest_price=11028.40,
                prev_close=10820.15,
                change_amount=208.25,
                change_pct=1.92,
                today_amount=6580e8,
                today_volume=5.92e8,
                vwap=10940.10,
                vwap_diff_pct=0.81,
                yoy_amount_ratio=1.38,
                yoy_amount_diff_pct=38.0,
                marginal_30m_amount=1540e8,
                marginal_30m_ratio=1.58,
                marginal_30m_diff_pct=58.0,
            ),
            IndexSnapshot(
                code="399006",
                name="创业板指",
                symbol="sz399006",
                latest_price=2318.80,
                prev_close=2256.50,
                change_amount=62.30,
                change_pct=2.76,
                today_amount=3120e8,
                today_volume=1.96e8,
                vwap=2292.40,
                vwap_diff_pct=1.15,
                yoy_amount_ratio=1.45,
                yoy_amount_diff_pct=45.0,
                marginal_30m_amount=780e8,
                marginal_30m_ratio=1.64,
                marginal_30m_diff_pct=64.0,
            ),
            IndexSnapshot(
                code="000688",
                name="科创50",
                symbol="sh000688",
                latest_price=1042.15,
                prev_close=1010.80,
                change_amount=31.35,
                change_pct=3.10,
                today_amount=1260e8,
                today_volume=0.58e8,
                vwap=1028.50,
                vwap_diff_pct=1.33,
                yoy_amount_ratio=1.48,
                yoy_amount_diff_pct=48.0,
                marginal_30m_amount=340e8,
                marginal_30m_ratio=1.70,
                marginal_30m_diff_pct=70.0,
            ),
        ]
        breadth = MarketBreadth(
            advancing=4210,
            declining=980,
            unchanged=165,
            limit_up=112,
            limit_down=4,
            total_turnover=11400e8,
        )
        sector_flows = SectorFlowSummary(
            industry_inflow_top=[
                SectorFlowItem("半导体", 4.18, 68.42, 7.12, "中芯国际"),
                SectorFlowItem("证券", 3.25, 54.15, 6.40, "东方财富"),
                SectorFlowItem("软件开发", 3.62, 41.30, 5.25, "科大讯飞"),
                SectorFlowItem("通信设备", 2.94, 32.80, 4.90, "中际旭创"),
                SectorFlowItem("电池", 2.40, 26.55, 3.88, "宁德时代"),
            ],
            industry_outflow_top=[
                SectorFlowItem("银行", -0.35, -14.20, -2.15, "工商银行"),
                SectorFlowItem("电力行业", -0.48, -9.85, -1.90, "长江电力"),
                SectorFlowItem("煤炭行业", -0.62, -7.40, -2.30, "中国神华"),
                SectorFlowItem("贵金属", -0.55, -5.12, -1.75, "山东黄金"),
                SectorFlowItem("公用事业", -0.22, -3.60, -1.10, "华能国际"),
            ],
            concept_inflow_top=[
                SectorFlowItem("算力概念", 4.35, 92.60, 6.85, "寒武纪"),
                SectorFlowItem("AI应用", 3.88, 74.10, 5.92, "金山办公"),
                SectorFlowItem("国产芯片", 4.02, 65.30, 6.10, "海光信息"),
                SectorFlowItem("人形机器人", 3.45, 48.20, 4.70, "三花智控"),
                SectorFlowItem("低空经济", 2.80, 31.45, 3.95, "万丰奥威"),
            ],
            concept_outflow_top=[
                SectorFlowItem("高股息精选", -0.42, -18.50, -2.10, "中国海油"),
                SectorFlowItem("中特估", -0.25, -12.30, -1.45, "中国石油"),
                SectorFlowItem("黄金概念", -0.60, -8.70, -1.80, "紫金矿业"),
                SectorFlowItem("煤炭概念", -0.58, -6.90, -1.95, "陕西煤业"),
                SectorFlowItem("ST板块", -0.85, -4.15, -2.60, "ST中青宝"),
            ],
        )
    elif scenario == "plunge":
        indices = [
            IndexSnapshot(
                code="000001",
                name="上证指数",
                symbol="sh000001",
                latest_price=3312.10,
                prev_close=3368.40,
                change_amount=-56.30,
                change_pct=-1.67,
                today_amount=4950e8,
                today_volume=4.52e8,
                vwap=3341.80,
                vwap_diff_pct=-0.89,
                yoy_amount_ratio=1.32,
                yoy_amount_diff_pct=32.0,
                marginal_30m_amount=1180e8,
                marginal_30m_ratio=1.55,
                marginal_30m_diff_pct=55.0,
            ),
            IndexSnapshot(
                code="399001",
                name="深证成指",
                symbol="sz399001",
                latest_price=10585.20,
                prev_close=10820.15,
                change_amount=-234.95,
                change_pct=-2.17,
                today_amount=6710e8,
                today_volume=6.15e8,
                vwap=10698.50,
                vwap_diff_pct=-1.06,
                yoy_amount_ratio=1.36,
                yoy_amount_diff_pct=36.0,
                marginal_30m_amount=1610e8,
                marginal_30m_ratio=1.60,
                marginal_30m_diff_pct=60.0,
            ),
            IndexSnapshot(
                code="399006",
                name="创业板指",
                symbol="sz399006",
                latest_price=2194.30,
                prev_close=2256.50,
                change_amount=-62.20,
                change_pct=-2.76,
                today_amount=3240e8,
                today_volume=2.08e8,
                vwap=2224.60,
                vwap_diff_pct=-1.36,
                yoy_amount_ratio=1.41,
                yoy_amount_diff_pct=41.0,
                marginal_30m_amount=820e8,
                marginal_30m_ratio=1.66,
                marginal_30m_diff_pct=66.0,
            ),
            IndexSnapshot(
                code="000688",
                name="科创50",
                symbol="sh000688",
                latest_price=979.40,
                prev_close=1010.80,
                change_amount=-31.40,
                change_pct=-3.11,
                today_amount=1310e8,
                today_volume=0.61e8,
                vwap=995.20,
                vwap_diff_pct=-1.59,
                yoy_amount_ratio=1.44,
                yoy_amount_diff_pct=44.0,
                marginal_30m_amount=360e8,
                marginal_30m_ratio=1.72,
                marginal_30m_diff_pct=72.0,
            ),
        ]
        breadth = MarketBreadth(
            advancing=720,
            declining=4495,
            unchanged=140,
            limit_up=18,
            limit_down=64,
            total_turnover=11660e8,
        )
        sector_flows = SectorFlowSummary(
            industry_inflow_top=[
                SectorFlowItem("贵金属", 1.85, 16.40, 4.10, "赤峰黄金"),
                SectorFlowItem("银行", 0.65, 12.80, 2.05, "农业银行"),
                SectorFlowItem("电力行业", 0.42, 7.20, 1.45, "长江电力"),
                SectorFlowItem("公用事业", 0.30, 4.10, 1.02, "国投电力"),
                SectorFlowItem("石油行业", 0.25, 3.15, 0.88, "中国海油"),
            ],
            industry_outflow_top=[
                SectorFlowItem("半导体", -3.92, -78.60, -8.25, "中芯国际"),
                SectorFlowItem("软件开发", -4.15, -62.40, -7.80, "浪潮信息"),
                SectorFlowItem("证券", -2.85, -51.20, -6.15, "东方财富"),
                SectorFlowItem("互联网服务", -3.60, -44.80, -6.90, "三六零"),
                SectorFlowItem("光伏设备", -3.10, -33.50, -5.40, "隆基绿能"),
            ],
            concept_inflow_top=[
                SectorFlowItem("黄金概念", 1.72, 19.20, 3.85, "山东黄金"),
                SectorFlowItem("高股息精选", 0.48, 14.60, 1.90, "中国神华"),
                SectorFlowItem("中特估", 0.22, 8.30, 1.12, "中国移动"),
                SectorFlowItem("电力改革", 0.35, 5.40, 1.25, "华能国际"),
                SectorFlowItem("水电概念", 0.40, 3.90, 1.15, "川投能源"),
            ],
            concept_outflow_top=[
                SectorFlowItem("算力概念", -4.25, -108.40, -8.40, "寒武纪"),
                SectorFlowItem("AI应用", -4.05, -86.20, -7.95, "昆仑万维"),
                SectorFlowItem("国产芯片", -3.80, -71.50, -7.20, "海光信息"),
                SectorFlowItem("人形机器人", -3.55, -54.10, -6.30, "鸣志电器"),
                SectorFlowItem("华为概念", -2.95, -49.80, -5.60, "润和软件"),
            ],
        )
    else:
        indices = [
            IndexSnapshot(
                code="000001",
                name="上证指数",
                symbol="sh000001",
                latest_price=3374.85,
                prev_close=3368.40,
                change_amount=6.45,
                change_pct=0.19,
                today_amount=3580e8,
                today_volume=3.21e8,
                vwap=3372.10,
                vwap_diff_pct=0.08,
                yoy_amount_ratio=1.03,
                yoy_amount_diff_pct=3.0,
                marginal_30m_amount=690e8,
                marginal_30m_ratio=1.05,
                marginal_30m_diff_pct=5.0,
            ),
            IndexSnapshot(
                code="399001",
                name="深证成指",
                symbol="sz399001",
                latest_price=10852.60,
                prev_close=10820.15,
                change_amount=32.45,
                change_pct=0.30,
                today_amount=4890e8,
                today_volume=4.42e8,
                vwap=10841.30,
                vwap_diff_pct=0.10,
                yoy_amount_ratio=1.04,
                yoy_amount_diff_pct=4.0,
                marginal_30m_amount=940e8,
                marginal_30m_ratio=1.08,
                marginal_30m_diff_pct=8.0,
            ),
            IndexSnapshot(
                code="399006",
                name="创业板指",
                symbol="sz399006",
                latest_price=2264.10,
                prev_close=2256.50,
                change_amount=7.60,
                change_pct=0.34,
                today_amount=2210e8,
                today_volume=1.38e8,
                vwap=2261.80,
                vwap_diff_pct=0.10,
                yoy_amount_ratio=0.98,
                yoy_amount_diff_pct=-2.0,
                marginal_30m_amount=425e8,
                marginal_30m_ratio=1.02,
                marginal_30m_diff_pct=2.0,
            ),
            IndexSnapshot(
                code="000688",
                name="科创50",
                symbol="sh000688",
                latest_price=1016.20,
                prev_close=1010.80,
                change_amount=5.40,
                change_pct=0.53,
                today_amount=860e8,
                today_volume=0.39e8,
                vwap=1013.40,
                vwap_diff_pct=0.28,
                yoy_amount_ratio=1.06,
                yoy_amount_diff_pct=6.0,
                marginal_30m_amount=175e8,
                marginal_30m_ratio=1.10,
                marginal_30m_diff_pct=10.0,
            ),
        ]
        breadth = MarketBreadth(
            advancing=2780,
            declining=2310,
            unchanged=265,
            limit_up=52,
            limit_down=11,
            total_turnover=8470e8,
        )
        sector_flows = SectorFlowSummary(
            industry_inflow_top=[
                SectorFlowItem("通信设备", 1.82, 21.45, 3.60, "中际旭创"),
                SectorFlowItem("消费电子", 1.45, 18.20, 3.10, "立讯精密"),
                SectorFlowItem("汽车整车", 1.18, 14.30, 2.75, "比亚迪"),
                SectorFlowItem("半导体", 0.96, 11.80, 1.95, "北方华创"),
                SectorFlowItem("医药商业", 0.85, 8.40, 2.20, "益丰药房"),
            ],
            industry_outflow_top=[
                SectorFlowItem("酿酒行业", -1.12, -16.50, -3.40, "贵州茅台"),
                SectorFlowItem("房地产开发", -1.35, -13.20, -3.85, "万科A"),
                SectorFlowItem("煤炭行业", -0.88, -9.40, -2.50, "中国神华"),
                SectorFlowItem("证券", -0.45, -8.10, -1.65, "中信证券"),
                SectorFlowItem("银行", -0.28, -6.90, -1.20, "招商银行"),
            ],
            concept_inflow_top=[
                SectorFlowItem("CPO概念", 2.15, 29.80, 4.20, "新易盛"),
                SectorFlowItem("AI手机", 1.60, 22.10, 3.35, "歌尔股份"),
                SectorFlowItem("智能驾驶", 1.42, 18.75, 2.90, "德赛西威"),
                SectorFlowItem("算力概念", 1.10, 15.60, 2.15, "工业富联"),
                SectorFlowItem("创新药", 0.95, 11.20, 2.05, "恒瑞医药"),
            ],
            concept_outflow_top=[
                SectorFlowItem("白酒概念", -1.18, -19.40, -3.55, "五粮液"),
                SectorFlowItem("租售同权", -1.28, -14.10, -3.60, "保利发展"),
                SectorFlowItem("高股息精选", -0.52, -11.30, -1.80, "长江电力"),
                SectorFlowItem("光伏概念", -0.76, -9.85, -2.25, "阳光电源"),
                SectorFlowItem("免税概念", -0.90, -6.40, -2.40, "中国中免"),
            ],
        )

    return MarketSnapshot(
        timestamp=ts,
        indices=indices,
        breadth=breadth,
        sector_flows=sector_flows,
        is_mock=True,
        warnings=[],
    )


def build_mock_daily_review(
    scenario: MockScenario = "normal",
    timezone: str = "Asia/Shanghai",
    now: datetime | None = None,
) -> DailyReview:
    """Generate a realistic mock DailyReview for --job review --mock testing."""
    from .daily_review import compute_sentiment

    tz = ZoneInfo(timezone)
    ts = now if now is not None else datetime.now(tz)
    market = build_mock_snapshot(scenario=scenario, timezone=timezone, now=ts)

    if scenario == "surge":
        pool = LimitPoolStats(
            limit_up_count=112,
            broken_count=18,
            limit_down_count=4,
            max_boards=6,
            seal_rate=round(112 / (112 + 18), 4),
            prev_limit_up_avg_pct=4.35,
            board_ladder=[
                LimitStockItem("002197", "证通电子", 10.01, 6, "算力", "封单3.20亿"),
                LimitStockItem("603019", "中科曙光", 10.00, 4, "半导体", "封单5.80亿"),
                LimitStockItem("002415", "海康威视", 9.98, 3, "AI应用", "封单2.10亿"),
                LimitStockItem("300496", "中科创达", 20.00, 2, "软件开发", "封单1.65亿"),
            ],
            top_industries=["半导体(14)", "软件开发(11)", "通信设备(9)", "证券(7)", "电池(6)"],
            available=True,
        )
        capital = CapitalFlowOverview(
            main_net_inflow=286.50,
            main_net_inflow_source="东方财富",
            southbound_net_buy=64.20,
            margin_balance=11385.40,
            margin_change=92.60,
            margin_date=ts.strftime("%Y%m%d"),
        )
    elif scenario == "plunge":
        pool = LimitPoolStats(
            limit_up_count=18,
            broken_count=42,
            limit_down_count=64,
            max_boards=2,
            seal_rate=round(18 / (18 + 42), 4),
            prev_limit_up_avg_pct=-3.12,
            board_ladder=[
                LimitStockItem("600988", "赤峰黄金", 10.01, 2, "贵金属", "封单1.40亿"),
            ],
            top_industries=["贵金属(5)", "公用事业(3)", "银行(2)"],
            available=True,
        )
        capital = CapitalFlowOverview(
            main_net_inflow=-412.80,
            main_net_inflow_source="东方财富",
            southbound_net_buy=-38.50,
            margin_balance=11210.00,
            margin_change=-85.40,
            margin_date=ts.strftime("%Y%m%d"),
        )
    else:
        pool = LimitPoolStats(
            limit_up_count=65,
            broken_count=24,
            limit_down_count=8,
            max_boards=4,
            seal_rate=round(65 / (65 + 24), 4),
            prev_limit_up_avg_pct=1.82,
            board_ladder=[
                LimitStockItem("002197", "证通电子", 9.98, 4, "计算机设备", "封单1.85亿"),
                LimitStockItem("002565", "顺灏股份", 10.05, 3, "包装印刷", "封单2.11亿"),
                LimitStockItem("300308", "中际旭创", 10.00, 2, "通信设备", "封单3.40亿"),
            ],
            top_industries=["通信设备(8)", "半导体(7)", "消费电子(6)", "汽车整车(5)", "医药商业(4)"],
            available=True,
        )
        capital = CapitalFlowOverview(
            main_net_inflow=-42.30,
            main_net_inflow_source="同花顺行业汇总",
            southbound_net_buy=32.45,
            margin_balance=11277.04,
            margin_change=-18.59,
            margin_date=ts.strftime("%Y%m%d"),
        )

    hot_stocks = [
        LimitStockItem("300059", "东方财富", 3.25 if scenario == "surge" else 0.45, extra="人气第1"),
        LimitStockItem("688981", "中芯国际", 4.18 if scenario == "surge" else 1.20, extra="人气第2"),
        LimitStockItem("688256", "寒武纪", 5.60 if scenario == "surge" else -0.80, extra="人气第3"),
        LimitStockItem("300308", "中际旭创", 2.94 if scenario == "surge" else 1.82, extra="人气第4"),
        LimitStockItem("002594", "比亚迪", 1.80 if scenario == "surge" else 1.18, extra="人气第5"),
    ]

    return DailyReview(
        trade_date=ts.strftime("%Y-%m-%d"),
        timestamp=ts,
        market=market,
        limit_pool=pool,
        capital=capital,
        hot_stocks=hot_stocks,
        sentiment=compute_sentiment(market, pool),
        is_mock=True,
        warnings=[],
    )


def build_mock_lhb_review(
    timezone: str = "Asia/Shanghai",
    now: datetime | None = None,
) -> LhbReview:
    """Generate a realistic mock dragon-tiger list (龙虎榜) review."""
    tz = ZoneInfo(timezone)
    ts = now if now is not None else datetime.now(tz)
    return LhbReview(
        trade_date=ts.strftime("%Y-%m-%d"),
        timestamp=ts,
        total_count=58,
        institution_buy_count=19,
        net_buy_top=[
            LhbItem("300308", "中际旭创", 11.42, 4.86, "日涨幅偏离值达7%", "3家机构买入"),
            LhbItem("688256", "寒武纪", 9.85, 3.42, "日换手率达20%", "2家机构买入"),
            LhbItem("002197", "证通电子", 9.98, 1.65, "连续三个交易日涨幅偏离值达20%", "知名游资主买"),
            LhbItem("300496", "中科创达", 14.20, 1.28, "日涨幅偏离值达7%", "1家机构买入"),
            LhbItem("002415", "海康威视", 8.50, 0.94, "日涨幅偏离值达7%", "深股通+机构买入"),
        ],
        net_sell_top=[
            LhbItem("000530", "冰山冷热", -6.45, -1.94, "日换手率达20%", "机构净卖出"),
            LhbItem("605378", "野马电池", -10.01, -1.32, "日跌幅偏离值达7%", "游资撤离"),
            LhbItem("002148", "北纬科技", -4.80, -0.88, "日振幅值达15%", "高位获利盘兑现"),
        ],
        is_mock=True,
        warnings=[],
    )


def build_mock_gold_snapshot(
    scenario: MockScenario = "normal",
    timezone: str = "Asia/Shanghai",
    now: datetime | None = None,
) -> GoldSnapshot:
    """Generate realistic mock gold quotes for normal, surge, or plunge."""
    tz = ZoneInfo(timezone)
    ts = now if now is not None else datetime.now(tz)
    usd_cny = 6.6610
    qtime = ts.strftime("%Y-%m-%d %H:%M:%S")

    if scenario == "surge":
        au_price, au_chg, au_30m = 782.40, 2.45, 1.35
        comex_price, comex_chg, comex_30m = 3685.50, 2.52, 1.40
        london_price, london_chg, london_30m = 3658.20, 2.38, 1.28
    elif scenario == "plunge":
        au_price, au_chg, au_30m = 744.80, -2.30, -1.25
        comex_price, comex_chg, comex_30m = 3492.40, -2.42, -1.32
        london_price, london_chg, london_30m = 3468.10, -2.28, -1.18
    else:
        au_price, au_chg, au_30m = 766.00, 0.42, 0.18
        comex_price, comex_chg, comex_30m = 3600.85, 0.55, 0.22
        london_price, london_chg, london_30m = 3575.99, 0.38, 0.15

    comex_cny = round(comex_price * usd_cny / 31.1034768, 2)
    london_cny = round(london_price * usd_cny / 31.1034768, 2)
    premium = round(au_price - london_cny, 2)

    return GoldSnapshot(
        timestamp=ts,
        quotes=[
            GoldQuote(
                key="AU9999",
                name="上海金 Au99.99",
                price=au_price,
                unit="元/克",
                prev_close=round(au_price / (1 + au_chg / 100.0), 2),
                change_pct=au_chg,
                high=round(au_price * 1.006, 2),
                low=round(au_price * 0.994, 2),
                cny_per_gram=au_price,
                change_30m_pct=au_30m,
                quote_time=qtime,
            ),
            GoldQuote(
                key="COMEX",
                name="COMEX黄金",
                price=comex_price,
                unit="美元/盎司",
                prev_close=round(comex_price / (1 + comex_chg / 100.0), 2),
                change_pct=comex_chg,
                high=round(comex_price * 1.008, 2),
                low=round(comex_price * 0.992, 2),
                cny_per_gram=comex_cny,
                change_30m_pct=comex_30m,
                quote_time=qtime,
            ),
            GoldQuote(
                key="LONDON",
                name="伦敦金",
                price=london_price,
                unit="美元/盎司",
                prev_close=round(london_price / (1 + london_chg / 100.0), 2),
                change_pct=london_chg,
                high=round(london_price * 1.007, 2),
                low=round(london_price * 0.993, 2),
                cny_per_gram=london_cny,
                change_30m_pct=london_30m,
                quote_time=qtime,
            ),
        ],
        usd_cny=usd_cny,
        premium_cny_per_gram=premium,
        is_mock=True,
        warnings=[],
    )
