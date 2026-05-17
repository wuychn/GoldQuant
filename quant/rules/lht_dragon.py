"""龙回头（急跌/急涨 + MA20）形态：纯函数，供加自选规则调用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quant.rules.daily_bar_zt import qualifies_as_zt_board_bar


@dataclass
class DragonPatternParams:
    lookback_days: int = 120
    observation_days: int = 30
    min_zt_run_days: int = 5
    main_board_zt_pct: float = 9.8
    cyb_zt_pct: float = 19.8
    exclude_one_word_from_zt_run: bool = True
    one_word_amp_pct_max: float = 0.12
    sharp_down_pct: float = 7.0
    sharp_up_pct: float = 7.0
    yin_die_min_consecutive_bearish_bars: int = 4
    ma_window: int = 20
    effective_break_below_ma_consecutive_days: int = 2


def _bar_close(bar: dict[str, Any]) -> float | None:
    try:
        v = bar.get("收盘", bar.get("close"))
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _bar_open(bar: dict[str, Any]) -> float | None:
    try:
        v = bar.get("开盘", bar.get("open"))
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct_vs_prev(history: list[dict[str, Any]], i: int) -> float | None:
    """第 i 根相对前一收盘涨跌幅%，优先用条形『涨跌幅』。"""
    bar = history[i]
    ch = bar.get("涨跌幅")
    if ch is not None:
        try:
            return float(ch)
        except (TypeError, ValueError):
            pass
    if i <= 0:
        return None
    c = _bar_close(bar)
    pc = _bar_close(history[i - 1])
    if c is None or pc is None or pc == 0:
        return None
    return (c - pc) / pc * 100


def _max_consecutive_bearish(
    history: list[dict[str, Any]],
    *,
    lo: int,
    hi: int,
) -> int:
    mx = cur = 0
    for i in range(lo, hi + 1):
        bo = _bar_open(history[i])
        bc = _bar_close(history[i])
        if (
            bo is not None
            and bc is not None
            and bc < bo
        ):
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    return mx


def _has_effective_ma_break(
    closes: list[float],
    *,
    lo: int,
    hi: int,
    ma_win: int,
    need_below: int,
) -> tuple[bool, str]:
    """收盘连续 need_below 日低于当日 MA(ma_win)，视为『有效跌破』。"""
    n = len(closes)
    if need_below <= 0 or ma_win <= 1 or lo >= n:
        return False, ""

    run = 0
    for i in range(lo, hi + 1):
        if i < ma_win - 1:
            run = 0
            continue
        s = sum(closes[i - ma_win + 1 : i + 1]) / ma_win
        if closes[i] + 1e-9 < s:
            run += 1
            if run >= need_below:
                return True, f"连续≥{need_below}日收于MA{ma_win}下方(至索引{i})"
        else:
            run = 0
    return False, ""


def _find_rightmost_qualifying_run(
    history: list[dict[str, Any]],
    code: str,
    p: DragonPatternParams,
) -> tuple[int, int] | None:
    """选取『最近』一段符合条件的连板，返回 (run_start, run_end) 闭合区间下标。"""
    n = len(history)
    earliest_re = max(1, n - int(p.lookback_days))
    best: tuple[int, int] | None = None
    last_end = -1

    i = 1
    while i < n:
        bar = history[i]
        prev = history[i - 1]
        if not isinstance(bar, dict) or not isinstance(prev, dict):
            i += 1
            continue
        if qualifies_as_zt_board_bar(
            bar,
            prev,
            code,
            main_pct=p.main_board_zt_pct,
            cyb_pct=p.cyb_zt_pct,
            exclude_one_word=p.exclude_one_word_from_zt_run,
            one_word_amp_pct_max=p.one_word_amp_pct_max,
        ):
            rs = i
            j = i
            while j + 1 < n:
                bn = history[j + 1]
                pr = history[j]
                if not isinstance(bn, dict) or not isinstance(pr, dict):
                    break
                if qualifies_as_zt_board_bar(
                    bn,
                    pr,
                    code,
                    main_pct=p.main_board_zt_pct,
                    cyb_pct=p.cyb_zt_pct,
                    exclude_one_word=p.exclude_one_word_from_zt_run,
                    one_word_amp_pct_max=p.one_word_amp_pct_max,
                ):
                    j += 1
                else:
                    break
            re = j
            if re < earliest_re:
                i = re + 1
                continue
            streak = re - rs + 1
            if streak >= p.min_zt_run_days:
                t0_candidate = re + 1
                hi_idx = n - 1
                od = int(p.observation_days)
                # 最后一根须在 [T0, T0+od-1] 内（自连板收尾后第一根非涨停日起算 od 个交易日）
                if t0_candidate <= hi_idx <= t0_candidate + od - 1:
                    if best is None or re > last_end:
                        best = (rs, re)
                        last_end = re
            i = re + 1
        else:
            i += 1

    return best


def evaluate_lht_dragon_watchlist(history: Any, code: str, params: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """是否符合龙回头加仓自选形态，返回 (ok, reason_detail, extras)。"""
    if not history or not isinstance(history, list) or len(history) < 22:
        return False, "历史行情不足", {}

    hb: list[dict[str, Any]] = [x for x in history if isinstance(x, dict)]
    if len(hb) < len(history):
        history = hb
        if len(history) < 22:
            return False, "历史行情中非 dict 混入或不足", {}

    p = DragonPatternParams(
        lookback_days=int(params.get("lookback_days", 120)),
        observation_days=int(params.get("observation_days", 30)),
        min_zt_run_days=int(params.get("min_zt_run_days", 5)),
        main_board_zt_pct=float(params.get("main_board_zt_pct", 9.8)),
        cyb_zt_pct=float(params.get("cyb_zt_pct", 19.8)),
        exclude_one_word_from_zt_run=bool(params.get("exclude_one_word_from_zt_run", True)),
        one_word_amp_pct_max=float(params.get("one_word_amp_pct_max", 0.12)),
        sharp_down_pct=float(params.get("sharp_down_pct", 7.0)),
        sharp_up_pct=float(params.get("sharp_up_pct", 7.0)),
        yin_die_min_consecutive_bearish_bars=int(params.get("yin_die_min_consecutive_bearish_bars", 4)),
        ma_window=int(params.get("ma_window", 20)),
        effective_break_below_ma_consecutive_days=int(params.get("effective_break_below_ma_consecutive_days", 2)),
    )

    n = len(history)
    if n < max(p.ma_window, 2):
        return False, "样本过短", {}

    last_prev = history[n - 2]
    today_bar = history[n - 1]
    if not isinstance(today_bar, dict) or not isinstance(last_prev, dict):
        return False, "最新K异常", {}

    if qualifies_as_zt_board_bar(
        today_bar,
        last_prev,
        code,
        main_pct=p.main_board_zt_pct,
        cyb_pct=p.cyb_zt_pct,
        exclude_one_word=p.exclude_one_word_from_zt_run,
        one_word_amp_pct_max=p.one_word_amp_pct_max,
    ):
        return False, "最新日仍为连板视角涨停（连板收尾未结束），不进入『连板结束后观察窗』语义", {}

    run = _find_rightmost_qualifying_run(history, code, p)
    if run is None:
        return False, f"近{p.lookback_days}日无{p.min_zt_run_days}+连板(非一字按配置)", {}

    rs, re = run
    T0 = re + 1
    hi_idx = n - 1
    od = int(p.observation_days)
    if T0 > hi_idx:
        return False, "连板块尾无后续日", {}

    if hi_idx > T0 + od - 1:
        return (
            False,
            f"T0(bar#{T0})起已超过{od}个交易日（当前末尾bar#{hi_idx}），观察期已过",
            {"T0_index": T0, "zt_run": (rs, re)},
        )

    hi = min(hi_idx, T0 + od - 1)

    closes: list[float] = []
    for bar in history:
        c = _bar_close(bar)
        if c is None:
            return False, "存在无效收盘价，无法复核MA/pct", {}
        closes.append(float(c))

    yin_mx = _max_consecutive_bearish(history, lo=T0, hi=hi)

    if yin_mx >= p.yin_die_min_consecutive_bearish_bars:
        return (
            False,
            f"观察窗内最大连续阴线{yin_mx}根≥阈值{p.yin_die_min_consecutive_bearish_bars}（阴跌）",
            {"T0_index": T0, "zt_run": (rs, re)},
        )

    broke, br_note = _has_effective_ma_break(
        closes,
        lo=T0,
        hi=hi,
        ma_win=p.ma_window,
        need_below=p.effective_break_below_ma_consecutive_days,
    )
    if broke:
        return False, f"观察窗内{br_note}", {"T0_index": T0, "zt_run": (rs, re)}

    down_i: int | None = None
    up_j: int | None = None
    for i in range(T0, hi + 1):
        pct = _pct_vs_prev(history, i)
        if pct is None:
            continue
        if pct <= -abs(p.sharp_down_pct):
            down_i = i
            break
    if down_i is None:
        return (
            False,
            f"观察窗[T0···]内无单日跌≥{p.sharp_down_pct}%",
            {"T0_index": T0, "zt_run": (rs, re)},
        )

    for j in range(down_i + 1, hi + 1):
        pct = _pct_vs_prev(history, j)
        if pct is None:
            continue
        if pct >= abs(p.sharp_up_pct):
            up_j = j
            break
    if up_j is None:
        return (
            False,
            f"急跌出现后至观察窗末尾无单日涨≥{p.sharp_up_pct}%",
            {"T0_index": T0, "zt_run": (rs, re), "sharp_down_day": down_i},
        )

    extras = {
        "T0_index": T0,
        "zt_run": (rs, re),
        "sharp_down_day": down_i,
        "sharp_up_day": up_j,
        "yin_max_consecutive": yin_mx,
    }
    return (
        True,
        (
            f"非一字连板段 bars[{rs}…{re}]，T0=bar#{T0}起{p.observation_days}交易日内;"
            f"阴跌阈值内(最大{yin_mx}连续阴); MA{p.ma_window}未{p.effective_break_below_ma_consecutive_days}连破;"
            f"急跌日idx{down_i}({-abs(p.sharp_down_pct)}%级), 反攻日idx{up_j}(+{abs(p.sharp_up_pct)}%级)"
        ),
        extras,
    )
