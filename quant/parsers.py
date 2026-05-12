"""JSON 提取、归一化、可读格式拼接。"""

import ast
import json
import re

from quant.config import OPTIONAL_STRATEGY_ALLOWED


# ---------------------------------------------------------------------------
# Low-level bracket/fence utilities
# ---------------------------------------------------------------------------

def _match_bracket_span(s: str, start: int, open_ch: str = "[", close_ch: str = "]") -> tuple[int, int] | None:
    if start >= len(s) or s[start] != open_ch:
        return None
    depth = 0
    i = start
    while i < len(s):
        c = s[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    return None


def _strip_markdown_fence(text: str) -> str:
    s = text.lstrip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        end_fence = s.find("```")
        if end_fence != -1:
            s = s[:end_fence]
    return s


def _json_loads_array_relaxed(raw: str) -> list | None:
    raw = raw.strip()
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else None
    except json.JSONDecodeError:
        pass
    try:
        arr = ast.literal_eval(raw)
        return arr if isinstance(arr, list) else None
    except (SyntaxError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Array extraction
# ---------------------------------------------------------------------------

def parse_first_json_array_from_text(text: str) -> tuple[list, str]:
    i = text.find("[")
    if i < 0:
        return [], text.strip()
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                raw = text[i:j + 1].replace("［", "[").replace("］", "]")
                arr = _json_loads_array_relaxed(raw.strip())
                tail = text[j + 1:].strip()
                if not isinstance(arr, list):
                    return [], text.strip()
                return arr, tail
    return [], text.strip()


def _section_heading_regex(keyword: str) -> list[str]:
    kw = re.escape(keyword)
    return [
        rf"(?:^|\n)(\s*(?:\d|[一二三四五六七八九十百千]+)\s*[,，、\.．]\s*{kw})\s*",
        rf"(?:^|\n)(\s*【\s*{kw}\s*】)\s*",
        rf"(?:^|\n)(\s*{kw})\s*[:：]?\s*",
    ]


def _find_section_tail_start(content: str, section_keyword: str) -> list[int]:
    tails: list[int] = []
    for pat in _section_heading_regex(section_keyword):
        for m in re.finditer(pat, content):
            tails.append(m.end())
    seen: set[int] = set()
    out: list[int] = []
    for t in tails:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_json_array_with_span(
    content: str, section_keyword: str, *, stock_keys: tuple[str, ...] = ("股票代码", "股票名称")
) -> tuple[list, tuple[int, int] | None]:
    def try_parse_from(abs_start_scan: int) -> tuple[list, tuple[int, int]] | None:
        max_len = min(8000, len(content) - abs_start_scan)
        if max_len <= 0:
            return None
        scan = content[abs_start_scan:abs_start_scan + max_len]
        for off, c in enumerate(scan):
            if c not in "[［":
                continue
            abs_base = abs_start_scan + off
            open_c = content[abs_base] if abs_base < len(content) else ""
            if open_c == "［":
                span = _match_bracket_span(content, abs_base, "［", "］")
            else:
                span = _match_bracket_span(content, abs_base, "[", "]")
            if not span:
                continue
            s0, s1 = span
            raw = content[s0:s1].replace("［", "[").replace("］", "]")
            raw_for_load = raw.strip()
            if raw_for_load.startswith("```"):
                raw_for_load = _strip_markdown_fence(raw_for_load).strip()
            arr = _json_loads_array_relaxed(raw_for_load)
            if arr is None or not isinstance(arr, list):
                continue
            if len(arr) == 0:
                return [], (s0, s1)
            if not all(isinstance(x, dict) for x in arr):
                continue
            if any(any(k in x for k in stock_keys) for x in arr):
                return arr, (s0, s1)
        return None

    for tail_start in reversed(_find_section_tail_start(content, section_keyword)):
        got = try_parse_from(tail_start)
        if got:
            return got[0], got[1]

    key = section_keyword
    pos = len(content)
    for _ in range(24):
        pos = content.rfind(key, 0, pos)
        if pos < 0:
            break
        got = try_parse_from(pos + len(key))
        if got:
            return got[0], got[1]
        pos -= 1

    return [], None


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _infer_optional_strategy_from_reason(reason: str) -> str | None:
    s = (reason or "").strip()
    if s.startswith("【涨停板战法】"):
        return "涨停板战法"
    if s.startswith("【龙回头战法】"):
        return "龙回头战法"
    return None


def normalize_optional_rows(rows: list) -> list:
    out: list = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        reason = str(r.get("加入自选原因", "") or r.get("自选原因", "") or r.get("原因", "") or "").strip()
        if not reason:
            reason = "未注明"
        tag_raw = str(r.get("战法", "") or r.get("策略战法", "") or "").strip()
        if tag_raw in OPTIONAL_STRATEGY_ALLOWED:
            tag = tag_raw
        else:
            inferred = _infer_optional_strategy_from_reason(reason)
            tag = inferred if inferred else "未标注"
        d = {
            "股票代码": str(r.get("股票代码", "") or "").strip(),
            "股票名称": str(r.get("股票名称", "") or "").strip(),
            "战法": tag,
            "加入自选原因": reason,
        }
        if d["股票代码"] or d["股票名称"]:
            out.append(d)
    return out


def normalize_holding_rows(rows: list) -> list:
    out: list = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = {
            "股票代码": str(r.get("股票代码", "") or "").strip(),
            "股票名称": str(r.get("股票名称", "") or "").strip(),
            "买入时间": str(r.get("买入时间", "") or "").strip(),
            "买入价": r.get("买入价", r.get("买入价格", "")),
            "买入原因": str(r.get("买入原因", "") or "").strip(),
            "卖出时间": str(r.get("卖出时间", "") or "").strip(),
            "卖出价": r.get("卖出价", r.get("卖出价格", "")),
            "卖出原因": str(r.get("卖出原因", "") or "").strip(),
        }
        if d["股票代码"] or d["股票名称"]:
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# Readable formatting
# ---------------------------------------------------------------------------

def holding_to_readable(h: dict) -> str:
    parts = [f"股票名称：{h.get('股票名称', '')}，股票代码：{h.get('股票代码', '')}"]
    for k, label in [("买入时间", "买入时间"), ("买入价", "买入价格"), ("买入原因", "买入原因"),
                     ("卖出时间", "卖出时间"), ("卖出价", "卖出价格"), ("卖出原因", "卖出原因")]:
        v = h.get(k)
        if v is not None and str(v).strip():
            parts.append(f"{label}：{v}")
    return "，".join(parts)


def optional_to_readable(o: dict) -> str:
    code = o.get("股票代码", "")
    name = o.get("股票名称", "")
    reason = o.get("加入自选原因", "")
    tag = str(o.get("战法", "") or "").strip()
    if tag and tag != "未标注":
        return f"股票名称：{name}，股票代码：{code}，战法：{tag}，加入自选原因：{reason}"
    return f"股票名称：{name}，股票代码：{code}，加入自选原因：{reason}"


def build_readable_block(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(f"{i}、{ln}" for i, ln in enumerate(lines, start=1))


def stitch_optional_section(label: str, arr_zt: list, arr_lht: list, tail_zt: str, tail_lht: str) -> str:
    norm_zt = normalize_optional_rows(arr_zt)
    norm_lht = normalize_optional_rows(arr_lht)
    merged_in = norm_zt + norm_lht
    merged = []
    seen: set = set()
    for row in merged_in:
        k = (row.get("股票代码", ""), row.get("战法", ""))
        if k in seen:
            continue
        seen.add(k)
        merged.append(row)
    lines = [label]
    if merged:
        lines.append(json.dumps(merged, ensure_ascii=False))
    else:
        lines.append("[]")

    def _collect_reason(tail_text: str) -> list[str]:
        out = []
        for ln in tail_text.splitlines():
            t = ln.strip()
            if t and ("原因" in t or "未更新" in t or "不达标" in t
                      or "不满足" in t or "排除" in t or "无合格" in t
                      or "不通过" in t or "超" in t or "不符" in t):
                out.append(t)
        if not out and tail_text.strip():
            out.append(tail_text.strip()[:200])
        return out

    if not norm_zt:
        zt_reasons = _collect_reason(tail_zt)
        if zt_reasons:
            lines.extend(zt_reasons)
        else:
            lines.append("涨停板战法自选未更新原因：无符合条件的标的（LLM未给出详细原因）")

    if not norm_lht:
        lht_reasons = _collect_reason(tail_lht)
        if lht_reasons:
            lines.extend(lht_reasons)
        else:
            lines.append("龙回头战法自选未更新原因：无符合条件的标的（LLM未给出详细原因）")

    return "\n".join(lines)
