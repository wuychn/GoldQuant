"""规则引擎基类：Rule、RuleResult、RuleChain。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RuleVerdict(Enum):
    """规则判定结果。"""
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"  # 规则不适用于当前场景


@dataclass
class RuleResult:
    """单条规则执行结果。"""
    verdict: RuleVerdict
    rule_name: str
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict == RuleVerdict.PASS

    @property
    def failed(self) -> bool:
        return self.verdict == RuleVerdict.FAIL

    @property
    def skipped(self) -> bool:
        return self.verdict == RuleVerdict.SKIP

    def __str__(self) -> str:
        tag = self.verdict.value.upper()
        return f"[{tag}] {self.rule_name}: {self.reason}" if self.reason else f"[{tag}] {self.rule_name}"


@dataclass
class ChainResult:
    """规则链执行结果汇总。"""
    chain_name: str
    results: list[RuleResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.verdict != RuleVerdict.FAIL for r in self.results)

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.results if r.verdict == RuleVerdict.FAIL]

    @property
    def passes(self) -> list[RuleResult]:
        return [r for r in self.results if r.verdict == RuleVerdict.PASS]

    @property
    def skips(self) -> list[RuleResult]:
        return [r for r in self.results if r.verdict == RuleVerdict.SKIP]

    def summary(self) -> str:
        """生成适合注入 LLM prompt 的摘要文本。"""
        if not self.results:
            return f"【{self.chain_name}】无规则执行"
        parts = [f"【{self.chain_name}】"]
        if self.all_passed:
            parts.append(f"全部通过（{len(self.passes)}条）")
        else:
            parts.append(f"未通过（{len(self.failures)}/{len(self.results)}条失败）")
            for f in self.failures:
                parts.append(f"  [X] {f.rule_name}：{f.reason}")
        for p in self.passes:
            if p.reason:
                parts.append(f"  [O] {p.rule_name}：{p.reason}")
        return "\n".join(parts)


class Rule(ABC):
    """规则抽象基类。子类实现 evaluate() 即可。

    子类可定义 default_params() 返回默认参数字典，
    registry 加载配置后通过 params.update() 覆盖。
    """

    def __init__(self, *, enabled: bool = True):
        self._enabled = enabled
        self.params: dict[str, Any] = self.default_params()

    def default_params(self) -> dict[str, Any]:
        """子类可覆盖，返回该规则的默认参数字典。"""
        return {}

    @property
    @abstractmethod
    def name(self) -> str:
        """规则名称（唯一标识）。"""
        ...

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @abstractmethod
    def evaluate(self, ctx: Any) -> RuleResult:
        """评估规则，返回 RuleResult。ctx 为 RuleContext 实例。"""
        ...

    # 辅助方法 ---------------------------------------------------------------

    def _pass(self, reason: str = "", **data: Any) -> RuleResult:
        return RuleResult(RuleVerdict.PASS, self.name, reason, data)

    def _fail(self, reason: str, **data: Any) -> RuleResult:
        return RuleResult(RuleVerdict.FAIL, self.name, reason, data)

    def _skip(self, reason: str = "") -> RuleResult:
        return RuleResult(RuleVerdict.SKIP, self.name, reason)

    def __repr__(self) -> str:
        state = "ON" if self._enabled else "OFF"
        return f"<{self.__class__.__name__} [{state}]>"


class RuleChain:
    """规则链：按顺序执行一组规则。

    Parameters
    ----------
    name : 链名称
    rules : 规则列表
    halt_on_first_failure : 遇到第一个 FAIL 时是否中止后续规则
    """

    def __init__(
        self,
        name: str,
        rules: list[Rule] | None = None,
        *,
        halt_on_first_failure: bool = False,
    ):
        self.name = name
        self.rules: list[Rule] = rules or []
        self.halt_on_first_failure = halt_on_first_failure

    def add(self, rule: Rule) -> "RuleChain":
        """链式添加规则。"""
        self.rules.append(rule)
        return self

    def evaluate(self, ctx: Any) -> ChainResult:
        """依次执行所有已启用的规则。"""
        result = ChainResult(chain_name=self.name)
        for rule in self.rules:
            if not rule.enabled:
                continue
            r = rule.evaluate(ctx)
            result.results.append(r)
            if r.failed and self.halt_on_first_failure:
                break
        return result

    def __len__(self) -> int:
        return len(self.rules)

    def __repr__(self) -> str:
        enabled_count = sum(1 for r in self.rules if r.enabled)
        return f"<RuleChain '{self.name}' rules={enabled_count}/{len(self.rules)}>"
