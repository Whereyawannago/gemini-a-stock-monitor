"""Command-line interface for A-share intraday, close review, LHB, and gold monitoring."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional

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
        description="A股智能盯盘 + 每日收盘复盘/龙虎榜 + 黄金价格监控（AKShare + MiniMax + 飞书卡片）",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--once",
        action="store_true",
        help="立即执行一次指定任务并退出",
    )
    mode_group.add_argument(
        "--schedule",
        action="store_true",
        help="启动 APScheduler 定时调度（盘中盯盘 + 收盘复盘 + 龙虎榜 + 黄金监控）",
    )
    parser.add_argument(
        "--job",
        choices=["intraday", "review", "lhb", "gold", "gold-poll"],
        default="intraday",
        help="配合 --once 执行的任务类型：intraday(盘中盯盘) / review(收盘复盘) / lhb(龙虎榜) / gold(黄金简报) / gold-poll(黄金异动轮询)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="使用高仿真模拟数据（不调用 AKShare 网络接口）",
    )
    parser.add_argument(
        "--mock-scenario",
        choices=["normal", "surge", "plunge"],
        default="normal",
        help="配合 --mock 使用的模拟行情场景：normal / surge / plunge",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：不实际发送飞书 Webhook，也不更新冷却状态文件，仅在终端输出内容",
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


def _print_output(
    header: str,
    body: str,
    delivery: Optional[Dict[str, Any]],
    print_json: bool,
) -> None:
    print("\n" + "=" * 60)
    print(header)
    print("-" * 60)
    print(body)
    if print_json and delivery and "payload" in delivery:
        print("-" * 60)
        print(json.dumps(delivery["payload"], ensure_ascii=False, indent=2))
    print("=" * 60)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env(env_file=args.env_file)
    setup_logging(settings.log_level)

    pipeline = MonitorPipeline(settings)

    # Default to --once when --mock, --dry-run, or a non-default --job is specified
    run_once_mode = args.once or (
        (args.mock or args.dry_run or args.job != "intraday") and not args.schedule
    )

    if run_once_mode:
        show_output = args.print_json or args.dry_run

        if args.job == "intraday":
            result = pipeline.run_once(
                mock=args.mock,
                mock_scenario=args.mock_scenario,
                dry_run=args.dry_run,
            )
            if show_output:
                _print_output(
                    header=(
                        f"预警级别: {result.alert.summary_tag} ({result.alert.effective_level.value})\n"
                        f"解盘来源: {result.analysis.source} ({result.analysis.model})"
                    ),
                    body=result.analysis.content,
                    delivery=result.delivery,
                    print_json=args.print_json,
                )
        elif args.job == "review":
            result = pipeline.run_daily_review(
                mock=args.mock,
                mock_scenario=args.mock_scenario,
                dry_run=args.dry_run,
            )
            if show_output:
                _print_output(
                    header=(
                        f"收盘复盘: {result.review.trade_date} | "
                        f"情绪 {result.review.sentiment.score:.0f}/100 ({result.review.sentiment.label})\n"
                        f"解盘来源: {result.analysis.source} ({result.analysis.model})"
                    ),
                    body=result.analysis.content,
                    delivery=result.delivery,
                    print_json=args.print_json,
                )
        elif args.job == "lhb":
            result = pipeline.run_lhb_review(
                mock=args.mock,
                dry_run=args.dry_run,
            )
            if show_output:
                _print_output(
                    header=(
                        f"龙虎榜复盘: {result.lhb.trade_date} | "
                        f"上榜 {result.lhb.total_count} 只 (机构买入 {result.lhb.institution_buy_count} 只)"
                    ),
                    body=result.summary,
                    delivery=result.delivery,
                    print_json=args.print_json,
                )
        else:
            mode = "poll" if args.job == "gold-poll" else "brief"
            result = pipeline.run_gold_check(
                mode=mode,
                mock=args.mock,
                mock_scenario=args.mock_scenario,
                dry_run=args.dry_run,
            )
            if show_output:
                if result.sent and result.analysis:
                    alert_desc = (
                        f"触发 {len(result.alerts)} 条预警"
                        if result.alerts
                        else "常规黄金简报"
                    )
                    _print_output(
                        header=(
                            f"黄金监控 ({mode}): {alert_desc}\n"
                            f"简评来源: {result.analysis.source} ({result.analysis.model})"
                        ),
                        body=result.analysis.content,
                        delivery=result.delivery,
                        print_json=args.print_json,
                    )
                else:
                    print("\n[黄金监控] 本次轮询未触发新预警（或行情缺失），未生成推送卡片。")
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
