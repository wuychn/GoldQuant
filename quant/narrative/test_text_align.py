"""列对齐工具测试。"""

from __future__ import annotations

import unittest

from quant.narrative.text_align import clip_display, display_width, pad_display


class TextAlignTests(unittest.TestCase):
    def test_display_width_cjk(self) -> None:
        self.assertEqual(display_width("芯片"), 4)
        self.assertEqual(display_width("AI"), 2)

    def test_pad_display(self) -> None:
        self.assertEqual(display_width(pad_display("芯片", 8)), 8)


if __name__ == "__main__":
    unittest.main()
