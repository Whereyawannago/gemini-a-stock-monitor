"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv

from .models import PriceLevel


@dataclass(frozen=True)
class IndexSpec:
    """Definition of a tracked A-share benchmark index."""

    code: str
    name: str
    symbol: str  # e.g. sh000001 / sz399001


DEFAULT_INDICES: Tuple[IndexSpec, ...] = (
    IndexSpec(code="000001", name="上证指数", symbol="sh000001"),
    IndexSpec(code="399001", name="深证成指", symbol="sz399001"),
    IndexSpec(code="399006", name="创业板指", symbol="sz399006"),
    IndexSpec(code="000688", name="科创50", symbol="sh000688"),
)

# 10:00~15:00 每半小时（避开 11:30~13:00 午间休市）
DEFAULT_SCHEDULE_TIMES: Tuple[Tuple[int, int], ...] = (
    (10, 0),
    (10, 30),
    (11, 0),
    (11, 30),
    (13, 30),
    (14, 0),
    (14, 30),
    (15, 0),
)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def parse_time_list(raw: str) -> Tuple[Tuple[int, int], ...]:
    """Parse "09:30,21:00" into ((9, 30), (21, 0)); invalid items are skipped."""
    result = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hh, mm = part.split(":")
            h, m = int(hh), int(mm)
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            result.append((h, m))
    return tuple(result)


def _env_time_list(
    key: str, default: Tuple[Tuple[int, int], ...]
) -> Tuple[Tuple[int, int], ...]:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    parsed = parse_time_list(raw)
    return parsed or default


def parse_price_levels(raw: str) -> Tuple[PriceLevel, ...]:
    """Parse "AU9999:below:750,COMEX:above:3700" into PriceLevel tuples."""
    levels = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        pieces = [p.strip() for p in part.split(":")]
        if len(pieces) != 3:
            continue
        key, direction, price = pieces
        direction = direction.lower()
        if direction not in {"above", "below"}:
            continue
        try:
            levels.append(PriceLevel(key=key.upper(), direction=direction, price=float(price)))
        except ValueError:
            continue
    return tuple(levels)


@dataclass
class Settings:
    """Runtime settings for the A-share monitor."""

    # MiniMax OpenAI-compatible API settings
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    minimax_model: str = "MiniMax-M3.1-Flash"
    minimax_max_tokens: int = 2048
    minimax_timeout: float = 30.0
    minimax_temperature: float = 0.3

    # Feishu webhook settings
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""
    feishu_timeout: float = 15.0

    # Quantitative alert thresholds & cooldown
    alert_cooldown_minutes: int = 60
    alert_volume_ratio_threshold: float = 1.20
    alert_marginal_ratio_threshold: float = 1.35
    alert_surge_change_pct: float = 0.60
    alert_plunge_change_pct: float = -0.60
    alert_surge_advance_ratio: float = 0.60
    alert_plunge_advance_ratio: float = 0.40

    # General & scheduler settings
    timezone: str = "Asia/Shanghai"
    state_dir: Path = field(default_factory=lambda: Path("./data"))
    log_level: str = "INFO"
    sector_top_n: int = 5
    indices: Tuple[IndexSpec, ...] = DEFAULT_INDICES
    schedule_times: Tuple[Tuple[int, int], ...] = DEFAULT_SCHEDULE_TIMES

    # Feature toggles
    enable_intraday_monitor: bool = True
    enable_daily_review: bool = True
    enable_lhb_review: bool = True
    enable_gold_monitor: bool = True

    # Daily close review (交易日)
    review_time: Tuple[int, int] = (15, 30)
    lhb_time: Tuple[int, int] = (18, 0)

    # Gold monitor
    gold_brief_times: Tuple[Tuple[int, int], ...] = ((9, 30), (21, 0))
    gold_poll_minutes: int = 5
    gold_alert_30m_pct: float = 1.0
    gold_alert_daily_pct: float = 2.0
    gold_cooldown_minutes: int = 60
    gold_price_levels: Tuple[PriceLevel, ...] = ()

    @property
    def alert_state_file(self) -> Path:
        return self.state_dir / "alert_state.json"

    @property
    def trade_calendar_cache_file(self) -> Path:
        return self.state_dir / "trade_calendar_cache.json"

    @property
    def gold_state_file(self) -> Path:
        return self.state_dir / "gold_state.json"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Settings":
        """Load settings from optional .env file and process environment."""
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            load_dotenv(override=False)

        return cls(
            minimax_api_key=os.getenv("MINIMAX_API_KEY", "").strip(),
            minimax_base_url=os.getenv(
                "MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"
            ).strip(),
            minimax_model=os.getenv("MINIMAX_MODEL", "MiniMax-M3.1-Flash").strip(),
            minimax_max_tokens=_env_int("MINIMAX_MAX_TOKENS", 2048),
            minimax_timeout=_env_float("MINIMAX_TIMEOUT", 30.0),
            minimax_temperature=_env_float("MINIMAX_TEMPERATURE", 0.3),
            feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
            feishu_webhook_secret=os.getenv("FEISHU_WEBHOOK_SECRET", "").strip(),
            feishu_timeout=_env_float("FEISHU_TIMEOUT", 15.0),
            alert_cooldown_minutes=_env_int("ALERT_COOLDOWN_MINUTES", 60),
            alert_volume_ratio_threshold=_env_float(
                "ALERT_VOLUME_RATIO_THRESHOLD", 1.20
            ),
            alert_marginal_ratio_threshold=_env_float(
                "ALERT_MARGINAL_RATIO_THRESHOLD", 1.35
            ),
            alert_surge_change_pct=_env_float("ALERT_SURGE_CHANGE_PCT", 0.60),
            alert_plunge_change_pct=_env_float("ALERT_PLUNGE_CHANGE_PCT", -0.60),
            alert_surge_advance_ratio=_env_float("ALERT_SURGE_ADVANCE_RATIO", 0.60),
            alert_plunge_advance_ratio=_env_float("ALERT_PLUNGE_ADVANCE_RATIO", 0.40),
            timezone=os.getenv("TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
            state_dir=Path(os.getenv("STATE_DIR", "./data").strip() or "./data"),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            sector_top_n=_env_int("SECTOR_TOP_N", 5),
            enable_intraday_monitor=_env_bool("ENABLE_INTRADAY_MONITOR", True),
            enable_daily_review=_env_bool("ENABLE_DAILY_REVIEW", True),
            enable_lhb_review=_env_bool("ENABLE_LHB_REVIEW", True),
            enable_gold_monitor=_env_bool("ENABLE_GOLD_MONITOR", True),
            review_time=_env_time_list("REVIEW_TIME", ((15, 30),))[0],
            lhb_time=_env_time_list("LHB_TIME", ((18, 0),))[0],
            gold_brief_times=_env_time_list("GOLD_BRIEF_TIMES", ((9, 30), (21, 0))),
            gold_poll_minutes=max(1, _env_int("GOLD_POLL_MINUTES", 5)),
            gold_alert_30m_pct=_env_float("GOLD_ALERT_30M_PCT", 1.0),
            gold_alert_daily_pct=_env_float("GOLD_ALERT_DAILY_PCT", 2.0),
            gold_cooldown_minutes=_env_int("GOLD_COOLDOWN_MINUTES", 60),
            gold_price_levels=parse_price_levels(os.getenv("GOLD_PRICE_LEVELS", "")),
        )
