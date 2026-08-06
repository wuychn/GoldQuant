"""因子 IC 报告脚本。

用法：
    python -m scripts.factors.ic_report --panel data/panel.parquet --out reports/ic.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from common.progress_log import log_progress_done, log_progress_error, log_progress_start
from quant.factors.ic import factor_ic_report, ic_decay
from quant.factors.registry import REGISTRY

_SCOPE = "ic_report"


def _factor_corr_matrix(rows, names: list[str]) -> dict[str, dict[str, float]]:
    """截面中性因子相关矩阵（全样本拼接）。"""
    cols: dict[str, list[float]] = {n: [] for n in names}
    # 按日对齐：只取同日都有值的样本对太重；简化为按 (date,code) 取可用值后两两相关
    by_key: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        vals = {}
        for n in names:
            v = r.neutral.get(n) if r.neutral else None
            if v is None:
                v = r.raw.get(n)
            if v is not None and np.isfinite(v):
                vals[n] = float(v)
        if vals:
            by_key[(r.date, r.code)] = vals
    # 填充列
    for vals in by_key.values():
        for n in names:
            cols[n].append(vals.get(n, np.nan))
    mat: dict[str, dict[str, float]] = {}
    for a in names:
        mat[a] = {}
        aa = np.array(cols[a], dtype=float)
        for b in names:
            bb = np.array(cols[b], dtype=float)
            mask = np.isfinite(aa) & np.isfinite(bb)
            if mask.sum() < 30:
                mat[a][b] = 0.0
                continue
            c = np.corrcoef(aa[mask], bb[mask])[0, 1]
            mat[a][b] = round(float(c) if c == c else 0.0, 3)
    return mat


def _factor_autocorr(rows, names: list[str], lag: int = 1) -> dict[str, float]:
    """因子自相关（换手代理）：相邻交易日同码因子值相关。"""
    by_code: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for r in rows:
        by_code[r.code].append((r.date, r))
    out: dict[str, float] = {}
    for n in names:
        pairs_a: list[float] = []
        pairs_b: list[float] = []
        for _code, seq in by_code.items():
            seq = sorted(seq, key=lambda x: x[0])
            for i in range(len(seq) - lag):
                r0, r1 = seq[i][1], seq[i + lag][1]
                v0 = r0.neutral.get(n) if r0.neutral else r0.raw.get(n)
                v1 = r1.neutral.get(n) if r1.neutral else r1.raw.get(n)
                if v0 is None or v1 is None:
                    continue
                if np.isfinite(v0) and np.isfinite(v1):
                    pairs_a.append(float(v0))
                    pairs_b.append(float(v1))
        if len(pairs_a) < 30:
            out[n] = 0.0
            continue
        c = np.corrcoef(pairs_a, pairs_b)[0, 1]
        out[n] = round(float(c) if c == c else 0.0, 3)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", default=None, help="默认 $QUANT_HOME/reports/ic/ic.json")
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=args.panel)
    try:
        try:
            import pandas as pd

            df = pd.read_parquet(args.panel)
        except ImportError:
            log_progress_error(_SCOPE, "失败", detail="需要 pyarrow")
            sys.exit(1)

        from quant.factors.base import FactorRow

        rows: list[FactorRow] = []
        for _, r in df.iterrows():
            meta = {}
            if "meta" in r and r["meta"] is not None:
                meta = json.loads(r["meta"]) if isinstance(r["meta"], str) else dict(r["meta"])
            # 读取序列化的多档前瞻收益（build_panel.py 写为独立 fwd 列），供 ic_decay 使用
            if "fwd" in r and r["fwd"] is not None:
                fwd = json.loads(r["fwd"]) if isinstance(r["fwd"], str) else dict(r["fwd"])
                if fwd:
                    meta["fwd"] = {int(k): float(v) for k, v in fwd.items()}
            rows.append(
                FactorRow(
                    date=str(r["date"]),
                    code=str(r["code"]),
                    name=str(r.get("name", "")),
                    industry=str(r.get("industry", "")),
                    log_mcap=float(r["log_mcap"]) if r.get("log_mcap") is not None else None,
                    raw=json.loads(r["raw"]) if isinstance(r["raw"], str) else dict(r["raw"]),
                    neutral=json.loads(r["neutral"]) if isinstance(r["neutral"], str) else dict(r["neutral"]),
                    forward_return_pct=float(r["forward_return_pct"]) if r.get("forward_return_pct") is not None else None,
                    meta=meta,
                )
            )

        names = REGISTRY.names()
        if not args.out:
            from quant.store.paths import reports_dir

            args.out = str(reports_dir("ic") / "ic.json")

        report = {
            "factors": factor_ic_report(rows, names),
            "ic_decay": {f: ic_decay(rows, f) for f in names},
            "corr_matrix": _factor_corr_matrix(rows, names),
            "autocorr_lag1": _factor_autocorr(rows, names, lag=1),
        }
        # 高相关对告警
        high_corr = []
        mat = report["corr_matrix"]
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                c = abs(mat.get(a, {}).get(b, 0.0))
                if c > 0.8:
                    high_corr.append({"a": a, "b": b, "corr": c})
        report["high_corr_pairs"] = high_corr

        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        log_progress_done(_SCOPE, "成功", detail=args.out)
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
