"""势头 A–F 叠加单元测试。"""

from __future__ import annotations

import unittest

from quant.strategy.intraday import _momentum_stack_ok


def _bars(closes: list[float], *, base_vol: float = 1000.0) -> list[dict]:
    out: list[dict] = []
    for i, close in enumerate(closes):
        opn = closes[i - 1] if i > 0 else close - 0.01
        out.append(
            {
                "open": opn,
                "close": close,
                "vol": base_vol + i * 100,
                "time": f"2026-06-16 10:{10 + i}:00",
            }
        )
    return out


class MomentumStackTests(unittest.TestCase):
    def test_all_pass_on_smooth_uptrend(self) -> None:
        closes = [10.0 + i * 0.04 for i in range(16)]
        ok, note = _momentum_stack_ok(_bars(closes), {})
        self.assertTrue(ok, note)

    def test_fails_on_downward_tail(self) -> None:
        closes = [10.0 + i * 0.05 for i in range(10)] + [10.4, 10.35, 10.2, 10.1, 10.0, 9.9]
        ok, note = _momentum_stack_ok(_bars(closes), {})
        self.assertFalse(ok)
        self.assertTrue(note.startswith(("A:", "B:", "D:", "E:", "F:")))


if __name__ == "__main__":
    unittest.main()
