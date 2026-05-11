"""Fixture replay helpers for reproducible local signal tests."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quant.config import DEFAULT_STRATEGY_CONFIG, load_strategy_config
from quant.data_source import DEFAULT_FIXTURE_ROOT, VALID_MODES, fixture_path_for_mode
from quant.paths import get_quant_paths
from quant.signals import generate_signal_report


def load_fixture(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def replay_fixture(
    mode: str,
    *,
    config_path: str | Path = DEFAULT_STRATEGY_CONFIG,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    config = load_strategy_config(config_path)
    payload = load_fixture(fixture_path_for_mode(DEFAULT_FIXTURE_ROOT, mode))
    report = generate_signal_report(payload, mode=mode, config=config).to_dict()

    if output_path is None:
        paths = get_quant_paths()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = paths.signal_dir / f"{stamp}-{mode}-signals.json"
    else:
        output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay quant/data/<mode> into deterministic signals.")
    parser.add_argument("--mode", required=True, choices=VALID_MODES, help="Pipeline mode stored in the signal report")
    parser.add_argument("--config", default=str(DEFAULT_STRATEGY_CONFIG), help="Strategy JSON config path")
    parser.add_argument("--output", default=None, help="Optional signal JSON output path")
    args = parser.parse_args()

    report = replay_fixture(args.mode, config_path=args.config, output_path=args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
