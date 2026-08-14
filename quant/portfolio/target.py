"""目标组合：排名 buffer → 等权/逆波动 → vol target → 约束 → 权重缓冲。

实现 backtest.PortfolioPolicy 协议。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quant.portfolio.buffer import apply_buffer, apply_rank_buffer
from quant.portfolio.constraints import (
    alpha_strength_weights,
    apply_concept_cap,
    apply_sector_cap,
    apply_single_cap,
    apply_style_cap,
    truncate_to_n,
)
from quant.portfolio.ewma import estimate_covariance_ewma
from quant.portfolio.mvo import optimize_mvo
from quant.portfolio.voltarget import (
    estimate_covariance,
    inv_vol_weights,
    realized_vol,
    scale_to_target_vol,
)


@dataclass
class TargetPortfolio:
    """主升波段目标组合策略。"""

    n_enter: int = 8
    n_exit: int = 15
    max_stocks: int = 10
    target_vol: float = 0.15
    max_weight: float = 0.25  # 计划单票 ≤ 25%
    sector_cap: float = 0.40
    concept_cap: float = 0.40
    full_invest: float = 0.95
    buffer_abs: float = 0.01
    buffer_rel: float = 0.20
    drop_tol: float = 0.015
    min_trade: float = 0.01  # |Δw| < min_trade 不交易（并入权重缓冲）
    vol_lookback: int = 60  # 波动率/协方差回看窗口（20→60，降噪；可配）
    equal_weight: bool = True  # 默认等权；False 时逆波动率
    alpha_weighted: bool = False  # True：alpha 强度配权（带收缩）
    alpha_shrink: float = 0.5  # 1=纯等权，0=纯 alpha 配权
    max_size_exposure: float = 0.0  # 小市值桶暴露上限（0=不约束）
    max_momentum_exposure: float = 0.0  # 高动量桶暴露上限（0=不约束）
    # 新建仓过滤：近 lookback 日收益 ≥ max_entry_ret_Nd 则不新开（None=关闭）
    max_entry_ret_5d: float | None = None
    entry_ret_lookback: int = 5
    optimizer: str = "rank_vol"  # rank_vol | mvo
    cov_method: str = "shrink"  # shrink | ewma
    mvo_risk_aversion: float = 2.0
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    sectors: dict[str, str] = field(default_factory=dict)
    concepts: dict[str, list[str]] = field(default_factory=dict)
    # SwapGate（值不值得换）；holding_buy_dates 由引擎/决策注入
    swap_gate: object | None = None  # SwapGateConfig | None
    holding_buy_dates: dict[str, str] = field(default_factory=dict)
    last_swap_decision: object | None = field(default=None, repr=False)

    @property
    def n(self) -> int:
        return self.max_stocks

    @classmethod
    def from_config(cls, *, daily=None, sectors=None, concepts=None, **kwargs) -> "TargetPortfolio":
        """从 quant.yml portfolio 段读约束（concept/industry cap + vol_lookback）。

        解决「TargetPortfolio 用 dataclass 默认值、运维改 yml 不生效」的配置脱节。
        其余参数（target_vol/max_weight/full_invest 等）用 dataclass 默认；可经 kwargs 覆盖。
        """
        from quant.config import load_quant_config
        from quant.portfolio.swap_gate import SwapGateConfig

        cfg = (load_quant_config().get("portfolio") or {})
        constraints = cfg.get("constraints") or {}
        risk_budget = cfg.get("risk_budget") or {}
        style = cfg.get("style_exposure") or {}
        entry_filter = cfg.get("entry_filter") or {}
        sg_raw = cfg.get("swap_gate") or {}
        swap_cfg = kwargs.pop("swap_gate", None)
        if swap_cfg is None:
            swap_cfg = SwapGateConfig.from_mapping(sg_raw) if sg_raw or "swap_gate" in cfg else None
        max_ret_5d = entry_filter.get("max_ret_5d", None)
        if max_ret_5d is not None:
            max_ret_5d = float(max_ret_5d)
            if max_ret_5d > 1.0:
                max_ret_5d /= 100.0
        lookback = int(entry_filter.get("lookback", 5))
        max_ret_5d = kwargs.pop("max_entry_ret_5d", max_ret_5d)
        lookback = int(kwargs.pop("entry_ret_lookback", lookback))
        # swap_gate 启用时：对齐持仓上限，并放宽单票上限以免 3 票装不满
        init_n = {}
        if swap_cfg is not None and getattr(swap_cfg, "enabled", False):
            if "n_enter" not in kwargs:
                init_n["n_enter"] = int(swap_cfg.n_enter)
            if "n_exit" not in kwargs:
                init_n["n_exit"] = int(swap_cfg.n_exit)
            if "max_stocks" not in kwargs:
                init_n["max_stocks"] = int(swap_cfg.max_stocks)
            if "max_weight" not in kwargs:
                init_n["max_weight"] = max(0.40, float(swap_cfg.full_invest) / max(int(swap_cfg.max_stocks), 1))
            if "equal_weight" not in kwargs:
                init_n["equal_weight"] = False
            # style.alpha_weighted 已读；若 yml 未写则默认开
            if "alpha_weighted" not in kwargs and not style:
                init_n["alpha_weighted"] = True
        return cls(
            sector_cap=float(constraints.get("max_industry_pct", 40)) / 100,
            concept_cap=float(constraints.get("max_concept_pct", 40)) / 100,
            vol_lookback=int(risk_budget.get("vol_lookback", 60)),
            alpha_weighted=bool(style.get("alpha_weighted", init_n.pop("alpha_weighted", False))),
            alpha_shrink=float(style.get("alpha_shrink", 0.5)),
            max_size_exposure=float(style.get("max_small_cap_pct", 0)) / 100,
            max_momentum_exposure=float(style.get("max_high_mom_pct", 0)) / 100,
            max_entry_ret_5d=max_ret_5d,
            entry_ret_lookback=lookback,
            optimizer=str(cfg.get("optimizer", "rank_vol")),
            cov_method=str((cfg.get("covariance") or {}).get("method", "shrink")),
            mvo_risk_aversion=float((cfg.get("optimizer_mvo") or {}).get("risk_aversion", 2.0)),
            daily=daily if daily is not None else pd.DataFrame(),
            sectors=sectors or {},
            concepts=concepts or {},
            swap_gate=swap_cfg,
            **init_n,
            **kwargs,
        )

    def _vols(self, codes: list[str], as_of: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in codes:
            v = realized_vol(self.daily, c, as_of, lookback=self.vol_lookback)
            if v is None or v < 1e-6:
                v = 0.30
            out[c] = v
        return out

    def _cov(
        self, codes: list[str], as_of: str
    ) -> tuple[dict[str, float], dict[str, dict[str, float]] | None]:
        """真实协方差矩阵（含收缩/EWMA）；数据不足回退到单资产波动率 + None。"""
        if self.cov_method == "ewma":
            est = estimate_covariance_ewma(self.daily, codes, as_of, lookback=self.vol_lookback)
        else:
            est = estimate_covariance(self.daily, codes, as_of, lookback=self.vol_lookback)
        if est is None:
            return self._vols(codes, as_of), None
        sigmas, covdict = est
        # 补全 estimate 落选的代码（用单资产波动回退）
        fallback = self._vols(codes, as_of)
        for c in codes:
            if c not in sigmas:
                sigmas[c] = fallback.get(c, 0.30)
        return sigmas, covdict

    def _style_buckets(self, codes: list[str], as_of: str) -> tuple[dict[str, str], dict[str, str]]:
        """size: small/large；momentum: high_mom/low_mom（截面分位，PIT）。"""
        size_b: dict[str, str] = {}
        mom_b: dict[str, str] = {}
        if self.daily.empty:
            return size_b, mom_b
        d = self.daily
        if not pd.api.types.is_string_dtype(d["date"]):
            d = d.copy()
            d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        mvs: dict[str, float] = {}
        moms: dict[str, float] = {}
        for c in codes:
            sub = d[(d["code"] == c) & (d["date"] <= as_of)].sort_values("date")
            if sub.empty:
                continue
            row = sub.iloc[-1]
            mv = float(row.get("float_mv") or row.get("total_mv") or 0)
            if mv > 0:
                mvs[c] = mv
            if len(sub) >= 61:
                c0 = float(sub.iloc[-61]["close"])
                c1 = float(sub.iloc[-1]["close"])
                if c0 > 0:
                    moms[c] = c1 / c0 - 1.0
        if len(mvs) >= 4:
            q = pd.Series(mvs).quantile(0.3)
            for c, mv in mvs.items():
                size_b[c] = "small" if mv <= q else "large"
        if len(moms) >= 4:
            q = pd.Series(moms).quantile(0.7)
            for c, m in moms.items():
                mom_b[c] = "high_mom" if m >= q else "low_mom"
        return size_b, mom_b

    def target_weights(self, alpha, prices, current, date):
        # 0. 新建仓动量过热过滤（已持仓不因过滤被踢出）
        if self.max_entry_ret_5d is not None and self.max_entry_ret_5d > 0 and not self.daily.empty:
            from quant.portfolio.entry_filters import filter_new_entries, overheat_codes

            held = {str(c) for c, w in (current or {}).items() if w and w > 0}
            # 只检查头部候选，避免对全 alpha 宇宙逐日扫盘
            top_n = max(self.n_enter * 5, self.max_stocks * 5, 40)
            ranked = sorted(alpha.keys(), key=lambda c: -alpha.get(c, -1e18))[:top_n]
            check = set(ranked) | held
            hot = overheat_codes(
                self.daily,
                date,
                check,
                lookback=self.entry_ret_lookback,
                max_ret=self.max_entry_ret_5d,
            )
            alpha = filter_new_entries(alpha, current or {}, hot)

        # 1. 选股：SwapGate（值不值得换）或排名 buffer
        self.last_swap_decision = None
        sg = self.swap_gate
        if sg is not None and getattr(sg, "enabled", False):
            from quant.portfolio.swap_gate import select_target_codes

            # 同步 gate 规模参数与组合一致
            sg.n_enter = self.n_enter
            sg.n_exit = self.n_exit
            sg.max_stocks = self.max_stocks
            sg.full_invest = self.full_invest
            adv_by_code: dict[str, float] = {}
            if not self.daily.empty:
                d = self.daily
                if not pd.api.types.is_string_dtype(d["date"]):
                    d = d.copy()
                    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
                # 粗 ADV：近 20 日 amount 均值
                for code in set(list(alpha.keys())[:80]) | set((current or {}).keys()):
                    sub = d[(d["code"].astype(str) == str(code)) & (d["date"] <= date)].tail(20)
                    if not sub.empty and "amount" in sub.columns:
                        adv_by_code[str(code)] = float(pd.to_numeric(sub["amount"], errors="coerce").mean() or 0)
            dec = select_target_codes(
                alpha,
                current or {},
                daily=self.daily,
                as_of=str(date),
                prices=prices or {},
                cfg=sg,
                buy_dates=self.holding_buy_dates or None,
                adv_by_code=adv_by_code,
            )
            self.last_swap_decision = dec
            codes = [c for c in dec.codes if c in prices]
        else:
            codes = apply_rank_buffer(
                alpha, current, n_enter=self.n_enter, n_exit=self.n_exit
            )
            codes = [c for c in codes if c in prices]
            if codes and len(codes) > self.max_stocks:
                codes = sorted(codes, key=lambda c: -alpha.get(c, -1e18))[: self.max_stocks]
        if not codes:
            return {}

        # 2/3. 权重 + 波动率目标：用真实协方差矩阵估组合波动（替代单一 ρ=0.3）
        sigmas, covdict = self._cov(codes, date)
        if self.optimizer == "mvo" and covdict is not None:
            size_b, mom_b = self._style_buckets(codes, date)
            style_groups: list[tuple[dict[str, str], float]] = []
            if self.max_size_exposure > 0 and size_b:
                style_groups.append((size_b, self.max_size_exposure))
            if self.max_momentum_exposure > 0 and mom_b:
                style_groups.append((mom_b, self.max_momentum_exposure))
            w = optimize_mvo(
                codes,
                alpha,
                covdict,
                sigmas,
                risk_aversion=self.mvo_risk_aversion,
                max_weight=self.max_weight,
                full_invest=self.full_invest,
                sectors=self.sectors if self.sectors else None,
                sector_cap=self.sector_cap,
                concepts=self.concepts if self.concepts else None,
                concept_cap=self.concept_cap,
                style_groups=style_groups or None,
            )
        elif self.alpha_weighted:
            w = alpha_strength_weights(alpha, codes, full_invest=self.full_invest, shrink=self.alpha_shrink)
        elif self.equal_weight:
            base = self.full_invest / len(codes)
            w = {c: base for c in codes}
        else:
            w = inv_vol_weights(sigmas, max_weight=self.max_weight)
        if self.target_vol > 0:
            w = scale_to_target_vol(w, sigmas, self.target_vol, cov=covdict)
            s = sum(w.values())
            if s > self.full_invest > 0:
                w = {c: v * (self.full_invest / s) for c, v in w.items()}

        # 4. 约束
        w = apply_single_cap(w, self.max_weight)
        if self.sectors:
            w = apply_sector_cap(w, self.sectors, self.sector_cap)
        if self.concepts:
            w = apply_concept_cap(w, self.concepts, self.concept_cap)
        size_b, mom_b = self._style_buckets(codes, date)
        if self.max_size_exposure > 0 and size_b:
            w = apply_style_cap(w, size_b, {"small": self.max_size_exposure})
        if self.max_momentum_exposure > 0 and mom_b:
            w = apply_style_cap(w, mom_b, {"high_mom": self.max_momentum_exposure})
        w = truncate_to_n(w, self.max_stocks)
        # truncate 删小权重票后总仓下降 → 归一化回 full_invest，避免长期欠仓
        _s = sum(w.values())
        if _s > 0 and self.full_invest > 0 and _s < self.full_invest:
            w = {c: v * (self.full_invest / _s) for c, v in w.items()}
            w = apply_single_cap(w, self.max_weight)  # 归一化放大后重校单票上限

        # 5. 权重缓冲（含 min_trade）
        abs_tol = max(self.buffer_abs, self.min_trade)
        w = apply_buffer(
            w, current, abs_tol=abs_tol, rel_tol=self.buffer_rel, drop_tol=self.drop_tol
        )
        # 清零极小权重
        return {c: v for c, v in w.items() if v > 1e-6}
