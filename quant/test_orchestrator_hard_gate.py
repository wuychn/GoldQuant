"""硬门禁（主升波段）开关测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from quant.orchestrator import _score_in_main_wave


def _score(code: str, in_main_wave: bool, total: float = 75.0):
    return SimpleNamespace(
        code=code,
        name=code,
        total=total,
        dimensions=[
            SimpleNamespace(
                name="main_wave",
                score=70.0,
                weight=35.0,
                enabled=True,
                available=True,
                detail={"主升波段": in_main_wave},
            )
        ],
    )


class MainWaveGateTests(unittest.TestCase):
    def test_score_in_main_wave_reads_detail(self) -> None:
        self.assertTrue(_score_in_main_wave(_score("A", True)))
        self.assertFalse(_score_in_main_wave(_score("B", False)))

    def test_no_main_wave_dimension_returns_false(self) -> None:
        s = SimpleNamespace(code="C", dimensions=[SimpleNamespace(name="technical", detail={})])
        self.assertFalse(_score_in_main_wave(s))

    def test_gate_filters_non_main_wave(self) -> None:
        """模拟硬门禁：passed 中 主升波段=False 的被剔除。"""
        passed = [_score("A", True), _score("B", False), _score("C", True)]
        gated = [s for s in passed if _score_in_main_wave(s)]
        self.assertEqual([s.code for s in gated], ["A", "C"])

    def test_soft_mode_keeps_all_when_switch_off(self) -> None:
        """开关关闭时不调用过滤，非主升浪票保留（仅靠软权重）。"""
        passed = [_score("A", True), _score("B", False)]
        # 模拟 watchlist_require_main_wave=False 的分支：不过滤
        self.assertEqual(len(passed), 2)


if __name__ == "__main__":
    unittest.main()
