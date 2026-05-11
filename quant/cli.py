"""Unified command line entry for GoldQuant workflows."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from quant.config import DEFAULT_STRATEGY_CONFIG, load_strategy_config
from quant.data_source import (
    DEFAULT_FIXTURE_ROOT,
    VALID_DATA_SOURCES,
    VALID_MODES,
    default_base_url,
    default_data_source,
    load_mode_data,
)
from quant.replay import replay_fixture
from quant.signals import generate_signal_report


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", required=True, choices=VALID_MODES, help="运行模式，例如 post_market_evening、news。")
    parser.add_argument(
        "--source",
        choices=VALID_DATA_SOURCES,
        default=default_data_source(),
        help="数据源：local 读取 quant/data/<mode>；remote 调用 FastAPI 实时接口，默认 remote。",
    )
    parser.add_argument(
        "--base-url",
        default=default_base_url(),
        help="source=remote 时使用的 GoldQuant API 地址。",
    )


def parse_run_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股短线辅助决策：支持本地 local 与实时 remote 两种数据源。",
    )
    add_run_arguments(parser)
    return parser.parse_args(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GoldQuant 统一命令入口。")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="运行完整辅助决策链路：取数、LLM 分析、状态更新、飞书推送。")
    add_run_arguments(run_parser)

    signal_parser = sub.add_parser("signal", help="读取 local/remote 数据并生成确定性结构化信号。")
    add_run_arguments(signal_parser)
    signal_parser.add_argument("--config", default=str(DEFAULT_STRATEGY_CONFIG), help="策略配置文件。")

    replay_parser = sub.add_parser("replay", help="按 --mode 回放 quant/data/<mode>，生成确定性结构化信号。")
    replay_parser.add_argument("--mode", required=True, choices=VALID_MODES)
    replay_parser.add_argument("--config", default=str(DEFAULT_STRATEGY_CONFIG), help="策略配置文件。")
    replay_parser.add_argument("--output", default=None, help="可选：信号 JSON 输出文件。")
    return parser


def _run_full_pipeline(args: argparse.Namespace) -> None:
    from quant import pipeline

    pipeline.run(
        args.mode,
        source=args.source,
        base_url=args.base_url,
    )


def _run_signal_only(args: argparse.Namespace) -> None:
    config = load_strategy_config(args.config)
    payload = load_mode_data(
        args.mode,
        source=args.source,
        project_root=DEFAULT_FIXTURE_ROOT,
        base_url=args.base_url,
    )
    report = generate_signal_report(payload, mode=args.mode, config=config).to_dict()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        _run_full_pipeline(args)
    elif args.command == "signal":
        _run_signal_only(args)
    elif args.command == "replay":
        report = replay_fixture(args.mode, config_path=args.config, output_path=args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        parser.error(f"未知命令: {args.command}")


if __name__ == "__main__":
    main()
