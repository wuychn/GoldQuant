#!/usr/bin/env python3
"""对比 service payload 与 fixture / 旧 HTTP 响应（Phase 0 基线工具）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MODES = (
    "news",
    "pre_market",
    "during_market",
    "post_market_lunch",
    "post_market_evening",
)


def _strip_internal(obj):
    if isinstance(obj, dict):
        return {
            k: _strip_internal(v)
            for k, v in obj.items()
            if k not in ("_session", "_degraded", "_observe_enriched")
        }
    if isinstance(obj, list):
        return [_strip_internal(v) for v in obj]
    return obj


def _diff(a, b, path: str = "") -> list[str]:
    diffs: list[str] = []
    if type(a) != type(b):
        return [f"{path}: type {type(a).__name__} != {type(b).__name__}"]
    if isinstance(a, dict):
        keys = sorted(set(a) | set(b))
        for k in keys:
            p = f"{path}.{k}" if path else k
            if k not in a:
                diffs.append(f"{p}: missing in A")
            elif k not in b:
                diffs.append(f"{p}: missing in B")
            else:
                diffs.extend(_diff(a[k], b[k], p))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f"{path}: len {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(_diff(x, y, f"{path}[{i}]"))
    elif a != b:
        diffs.append(f"{path}: {a!r} != {b!r}")
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff market mode payloads")
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--fixture", action="store_true", help="对比 service vs data/*.json")
    args = parser.parse_args()

    from app.core.config import get_settings
    from quant.data_fetch import load_mode_fixture
    from quant.services.market.payload import build_mode_payload

    settings = get_settings()
    br = build_mode_payload(args.mode, settings)
    service_data = _strip_internal(br.payload)

    if args.fixture:
        ref = load_mode_fixture(args.mode).get("data", load_mode_fixture(args.mode))
        ref = _strip_internal(ref)
    else:
        ref = service_data

    diffs = _diff(ref, service_data)
    if diffs:
        print(f"DIFF {args.mode}: {len(diffs)} field(s)")
        for d in diffs[:50]:
            print(" ", d)
        return 1
    print(f"OK {args.mode}: identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
