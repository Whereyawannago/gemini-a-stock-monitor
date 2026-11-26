"""AKShare compatibility layer & Eastmoney rate-limit circuit breaker.

1. AKShare renamed many interfaces in 1.20.44 / 1.20.45 / 1.20.46 (e.g.
   ``stock_zh_index_spot_em`` -> ``index_zh_spot_em``). Accessing an old name on
   a new release raises ``AttributeError`` (and vice versa on old releases), so
   all AKShare calls in this project go through :func:`ak_call`, which tries the
   new name first and falls back to legacy aliases.
2. Eastmoney ``push2`` quote servers temporarily ban an IP that sends too many
   requests and repeated calls during the ban extend it. When a rate-limit error
   is detected, :data:`EASTMONEY_GUARD` short-circuits further push2 calls for a
   cool-down window so callers fall back to Sina / THS / Legu sources at once.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Tuple

logger = logging.getLogger(__name__)

# canonical (new) name -> legacy aliases, tried in order
API_ALIASES: Dict[str, Tuple[str, ...]] = {
    # 指数实时行情 / 分钟K线 / 日K线
    "index_zh_spot_em": ("stock_zh_index_spot_em",),
    "index_zh_spot_sina": ("stock_zh_index_spot_sina",),
    "index_zh_hist_min_em": ("index_zh_a_hist_min_em",),
    "index_zh_daily_em": ("stock_zh_index_daily_em",),
    "stock_zh_a_hist_min_sina": ("stock_zh_a_minute",),
    # 资金流向
    "stock_fund_flow_sector_rank_em": ("stock_sector_fund_flow_rank",),
    "stock_fund_flow_market_em": ("stock_market_fund_flow",),
    "stock_fund_flow_industry_ths": ("stock_fund_flow_industry",),
    "stock_fund_flow_concept_ths": ("stock_fund_flow_concept",),
    # 交易日历
    "calendar_trade_date_hist_sina": ("tool_trade_date_hist_sina",),
    # 黄金 / 外盘期货 / 外汇
    "spot_realtime_sge": ("spot_quotations_sge",),
    "futures_foreign_realtime_sina": ("futures_foreign_commodity_realtime",),
    "futures_zh_realtime_sina": ("futures_zh_realtime",),
}

# Interfaces served by Eastmoney push2 quote servers (subject to IP rate limits)
EASTMONEY_PUSH2_APIS = frozenset(
    {
        "index_zh_spot_em",
        "index_zh_hist_min_em",
        "index_zh_daily_em",
        "stock_zh_a_spot_em",
        "stock_fund_flow_sector_rank_em",
        "stock_fund_flow_market_em",
    }
)


class EastmoneyBlockedError(RuntimeError):
    """Raised instead of calling Eastmoney push2 while the circuit is open."""


def is_rate_limit_error(exc: BaseException) -> bool:
    """Detect Eastmoney push2 rate limiting (AKShare Push2RateLimitError etc.)."""
    if isinstance(exc, EastmoneyBlockedError):
        return True
    name = type(exc).__name__
    text = str(exc)
    return "RateLimit" in name or "限流" in text


class _EastmoneyGuard:
    """Thread-safe circuit breaker for Eastmoney push2 endpoints."""

    def __init__(self, block_seconds: float = 600.0) -> None:
        self.block_seconds = block_seconds
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def is_blocked(self) -> bool:
        with self._lock:
            return time.monotonic() < self._blocked_until

    def block(self, reason: str = "") -> None:
        with self._lock:
            self._blocked_until = time.monotonic() + self.block_seconds
        logger.warning(
            "东方财富 push2 触发限流，%d 秒内跳过东方财富行情接口并改用备用数据源。%s",
            int(self.block_seconds),
            reason[:120],
        )

    def reset(self) -> None:
        with self._lock:
            self._blocked_until = 0.0


EASTMONEY_GUARD = _EastmoneyGuard()


def _import_akshare() -> Any:
    import akshare as ak  # lazy import: offline unit tests never need network

    return ak


def resolve(name: str, ak_module: Any | None = None) -> Callable[..., Any]:
    """Return the AKShare callable for ``name`` (new name or any legacy alias)."""
    ak = ak_module if ak_module is not None else _import_akshare()
    candidates = (name,) + API_ALIASES.get(name, ())
    errors = []
    for candidate in candidates:
        try:
            func = getattr(ak, candidate)
        except AttributeError as exc:
            errors.append(f"{candidate}: {str(exc)[:80]}")
            continue
        if callable(func):
            return func
    raise AttributeError(
        f"AKShare 中找不到接口 {name}（已尝试 {', '.join(candidates)}）: {errors}"
    )


def ak_call(name: str, *args: Any, ak_module: Any | None = None, **kwargs: Any) -> Any:
    """Invoke an AKShare interface by canonical name with legacy-alias fallback."""
    is_push2 = name in EASTMONEY_PUSH2_APIS
    if is_push2 and EASTMONEY_GUARD.is_blocked():
        raise EastmoneyBlockedError(f"东方财富限流冷却中，跳过 {name}")
    func = resolve(name, ak_module=ak_module)
    try:
        return func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        if is_push2 and is_rate_limit_error(exc):
            EASTMONEY_GUARD.block(str(exc))
        raise
