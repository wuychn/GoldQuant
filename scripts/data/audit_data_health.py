"""数据健康诊断：报告复权 / 名称 / 行业 / universe 的 PIT 覆盖率。

定位"接口在、前提缺"的数据盲区（第二轮审查发现：复权统一接口就绪但 adj_factor 未全量
落库、ST/退市 PIT 过滤因 name 列缺失而空转）。回测可信度的前提是这些覆盖到位。

用法：
    python -m scripts.data.audit_data_health
    python -m scripts.data.audit_data_health --probe-code 000001
"""

from __future__ import annotations

import argparse
import sys

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "audit_data_health"


def _line(k: str, v) -> None:
    print(f"  {k:<30} {v}")


def _snapshot_dates(subdir: str) -> list[str]:
    from quant.store.paths import quant_home

    d = quant_home() / "store" / subdir
    if not d.is_dir():
        return []
    files = sorted(d.glob("year=*/*.parquet"))
    return sorted({f.stem for f in files})


def audit_dailies() -> None:
    from quant.data.store import read_adj_factor, read_daily_raw

    print("[1] daily_raw / adj_factor 覆盖")
    raw = read_daily_raw()
    if raw.empty:
        print("  daily_raw 为空——请先 python -m scripts.data.build_daily")
        return
    raw_codes = set(raw["code"].astype(str).unique())
    dates = sorted(raw["date"].astype(str).unique())
    if "name" in raw.columns:
        names = raw["name"].astype(str).str.strip()
        nonempty = (names != "") & (names.str.lower() != "nan")
        name_cov = float(nonempty.sum()) / max(len(raw), 1)
    else:
        name_cov = 0.0
    _line("daily_raw 行数", len(raw))
    _line("daily_raw 代码数", len(raw_codes))
    _line("daily_raw 日期范围", f"{dates[0]} .. {dates[-1]} ({len(dates)} 日)")
    _line("daily_raw.name 非空率", f"{name_cov:.1%}")
    if name_cov < 0.95:
        print(
            "  ⚠ daily_raw.name 覆盖不足：ST/退市 PIT 过滤在历史日退化。"
            "回测前请确认 name_snapshot 已回填（update_daily 每日落）。",
            file=sys.stderr,
        )

    adj = read_adj_factor()
    if adj.empty:
        print(
            "  ⚠ adj_factor 为空——load_adjusted_daily 返回未复权价！"
            "请 build_daily（默认拉复权因子）。",
            file=sys.stderr,
        )
        return
    adj_codes = set(adj["code"].astype(str).unique())
    cov = len(adj_codes & raw_codes) / max(len(raw_codes), 1)
    _line("adj_factor 代码数", len(adj_codes))
    _line("adj_factor 覆盖率(vs daily_raw)", f"{cov:.1%}")
    if cov < 0.9:
        print(
            "  ⚠ 复权覆盖率 <90%：因子/IC/回测在未覆盖票上用未复权价（除权日假跳空）。",
            file=sys.stderr,
        )


def audit_name_snapshot() -> None:
    print("[2] name_snapshot PIT 覆盖")
    dates = _snapshot_dates("name_snapshot")
    if not dates:
        print("  无 name_snapshot（update_daily 未落过——ST/退市 PIT 过滤依赖它）")
        return
    _line("name_snapshot 日数", len(dates))
    _line("name_snapshot 日期范围", f"{dates[0]} .. {dates[-1]}")


def audit_industry() -> None:
    print("[3] industry PIT 快照")
    dates = _snapshot_dates("industry")
    if not dates:
        print("  无 industry 快照（中性化将全市场退化——panel_builder 不再回退当前映射）")
        return
    _line("industry 快照日数", len(dates))
    _line("industry 最早快照", dates[0])
    _line("industry 最新快照", dates[-1])


def audit_universe() -> None:
    print("[4] universe PIT 快照")
    dates = _snapshot_dates("universe")
    if not dates:
        print("  无 universe 快照")
        return
    _line("universe 快照日数", len(dates))
    _line("universe 最早", dates[0])
    _line("universe 最新", dates[-1])


def probe_akshare_name(code: str) -> None:
    print(f"[5] akshare stock_zh_a_hist name 列探测（code={code}）")
    try:
        import akshare as ak

        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date="20240101", end_date="20240110", adjust="")
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] akshare 探测失败（无网/未装）: {e}", file=sys.stderr)
        return
    if df is None or df.empty:
        print("  返回空")
        return
    cols = list(df.columns)
    name_col = next((c for c in cols if "名称" in str(c) or "name" in str(c).lower()), None)
    _line("返回列", cols)
    _line("含名称列", name_col is not None)
    if name_col is None:
        print(
            "  ⚠ stock_zh_a_hist 不返回名称列——daily_raw.name 历史必空，"
            "ST/退市 PIT 过滤完全依赖 name_snapshot。",
            file=sys.stderr,
        )
    else:
        sample = list(df[name_col].astype(str).unique()[:5])
        _line("名称样本", sample)
        print(
            "  ℹ 若名称随查询时点变化（如下拉历史段名称有变），则 stock_zh_a_hist 返回的是"
            "查询当下的当前名（非 PIT），历史 ST 过滤仍需依赖 name_snapshot。"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="数据健康诊断：复权/名称/行业/universe PIT 覆盖率")
    add_home_argument(ap)
    ap.add_argument("--probe-code", default="000001", help="akshare stock_zh_a_hist 名称列探测用的股票代码（默认 000001）")
    args = ap.parse_args()

    with home_context(args.home):
        log_progress_start(_SCOPE, "开始", detail=f"probe-code={args.probe_code}")
        try:
            log_progress(_SCOPE, "审计 daily_raw / adj_factor …")
            audit_dailies()
            print()
            log_progress(_SCOPE, "审计 name_snapshot …")
            audit_name_snapshot()
            print()
            log_progress(_SCOPE, "审计 industry …")
            audit_industry()
            print()
            log_progress(_SCOPE, "审计 universe …")
            audit_universe()
            print()
            log_progress(_SCOPE, "探测 akshare name 列 …")
            probe_akshare_name(args.probe_code)
            log_progress_done(_SCOPE, "成功")
        except Exception as e:
            log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
            raise


if __name__ == "__main__":
    main()
