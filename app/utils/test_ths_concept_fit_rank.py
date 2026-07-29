"""同花顺 F10 概念粘合度 HTML 解析测试。"""

from __future__ import annotations

from quant.data.sources.ths.concept_fit_rank import parse_concept_fit_ranks

_SAMPLE_HTML = """
<div class="f14 newconcept">
<a class='newtaid ccept_top1' title="排名第1">超级电容</a>
<a class='newtaid' title="排名第2">共封装光学(CPO)</a>
</div>
"""


def test_parse_concept_fit_ranks() -> None:
    rows = parse_concept_fit_ranks(_SAMPLE_HTML)
    assert len(rows) == 2
    assert rows[0].rank == 1
    assert rows[0].concept == "超级电容"
    assert rows[1].rank == 2
    assert rows[1].concept == "共封装光学(CPO)"
