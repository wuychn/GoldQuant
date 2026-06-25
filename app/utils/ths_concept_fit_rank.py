"""同花顺 F10 个股页「概念贴合度排名」抓取与解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

_BASE_URL = "https://basic.10jqka.com.cn/{symbol}/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_CN_RANK = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass(frozen=True)
class ConceptFitRank:
    rank: int
    concept: str
    hint: str | None = None


def normalize_symbol(symbol: str) -> str:
    code = symbol.strip().upper()
    if code.startswith(("SH", "SZ")):
        code = code[2:]
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无效股票代码: {symbol!r}")
    return code


def _parse_cn_rank(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token in _CN_RANK:
        return _CN_RANK[token]
    if len(token) == 2 and token[0] == "十" and token[1] in _CN_RANK:
        return 10 + _CN_RANK[token[1]]
    return None


def _parse_rank(title: str | None, tag_html: str, fallback: int) -> int:
    if title:
        m = re.search(r"排名第([一二三四五六七八九十\d]+)", title)
        if m:
            parsed = _parse_cn_rank(m.group(1))
            if parsed is not None:
                return parsed
    for cls, rank in (("ccept_top1", 1), ("ccept_top2", 2), ("ccept_top3", 3)):
        if cls in tag_html:
            return rank
    return fallback


def _extract_concept_block(html: str) -> str:
    m = re.search(r'<div class="f14\s+newconcept"[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        raise ValueError("页面中未找到概念贴合度区块（class=newconcept）")
    return m.group(1)


def parse_concept_fit_ranks(html: str) -> list[ConceptFitRank]:
    """从同花顺 F10 公司概要页 HTML 解析概念贴合度排名。"""
    block = _extract_concept_block(html)
    tags = re.findall(r"<a class='newtaid[^']*'[^>]*>.*?</a>", block, re.DOTALL)
    if not tags:
        raise ValueError("概念贴合度区块中未找到概念链接")

    rows: list[ConceptFitRank] = []
    for idx, tag in enumerate(tags, start=1):
        title_m = re.search(r'title="([^"]*)"', tag)
        hint = title_m.group(1).strip() if title_m else None
        concept = re.sub(r"<[^>]+>", "", tag).strip()
        if not concept:
            continue
        rows.append(
            ConceptFitRank(
                rank=_parse_rank(hint, tag, idx),
                concept=concept,
                hint=hint,
            )
        )
    rows.sort(key=lambda x: x.rank)
    return rows


def fetch_concept_fit_ranks(symbol: str, *, timeout: float = 30.0) -> list[ConceptFitRank]:
    """请求同花顺 F10 并返回概念贴合度排名列表。"""
    code = normalize_symbol(symbol)
    url = _BASE_URL.format(symbol=code)
    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": "https://basic.10jqka.com.cn/",
    }
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    html = resp.content.decode("gbk", errors="replace")
    return parse_concept_fit_ranks(html)


def get_concept_fit_rank_list(symbol: str, *, timeout: float = 30.0) -> list[dict[str, Any]]:
    """抓取指定股票的概念贴合度排名（rank 越小越相关）。"""
    return [
        {"rank": row.rank, "concept": row.concept}
        for row in fetch_concept_fit_ranks(symbol, timeout=timeout)
    ]
