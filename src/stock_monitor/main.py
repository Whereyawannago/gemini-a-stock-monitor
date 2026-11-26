"""Command-line interface for the A-share half-hour intelligent monitor."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from .config import Settings
from .pipeline import MonitorPipeline
from .scheduler import run_scheduler


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-monitor",
        description="A股半小时智能盯盘程序（AKShare + 量化预警 + MiniMax解盘 + 飞书卡片）",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--once",
        action="store_true",
        help="立即执行一次盯盘分析并退出",
    )
    mode_group.add_argument(
        "--schedule",
        action="store_true",
        help="启动 APScheduler 交易日历定时调度（10:00~15:00 每半小时）",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="使用高仿真模拟盘面数据（不调用 AKShare 网络接口）",
    )
    parser.add_argument(
        "--mock-scenario",
        choices=["normal", "surge", "plunge"],
        default="normal",
        help="配合 --mock 使用的模拟行情场景：normal(常规蓝色) / surge(放量上涨红色) / plunge(放量下跌绿色)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：不实际发送飞书 Webhook，也不更新冷却状态文件，仅在终端输出卡片内容",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="在 --once 模式下将完整飞书交互卡片 JSON 打印到标准输出",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="指定自定义 .env 配置文件路径",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env(env_file=args.env_file)
    setup_logging(settings.log_level)

    pipeline = MonitorPipeline(settings)

    # If neither --once nor --schedule is explicitly passed:
    # default to --once when --mock or --dry-run is used, else --schedule.
    run_once_mode = args.once or ((args.mock or args.dry_run) and not args.schedule)

    if run_once_mode:
        result = pipeline.run_once(
            mock=args.mock,
            mock_scenario=args.mock_scenario,
            dry_run=args.dry_run,
        )
        if args.print_json or args.dry_run:
            card_payload = result.delivery.get("payload", {})
            print("\n" + "=" * 60)
            print(f"预警级别: {result.alert.summary_tag} ({result.alert.effective_level.value})")
            print(f"解盘来源: {result.analysis.source} ({result.analysis.model})")
            print("-" * 60)
            print(result.analysis.content)
            if args.print_json:
                print("-" * 60)
                print(json.dumps(card_payload, ensure_ascii=False, indent=2))
            print("=" * 60)
        return 0

    run_scheduler(
        settings=settings,
        pipeline=pipeline,
        mock=args.mock,
        mock_scenario=args.mock_scenario,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
