"""行业映射人工规则：禁止错误自动匹配、强制正确指向。"""

from __future__ import annotations

from typing import Any

# 东财名 → 同花顺 canonical（优先于自动推断）
MANUAL_HINTS: dict[str, str] = {
    "休闲食品": "食品加工制造",
    "玻璃玻纤": "非金属材料",
    "工程咨询服务": "其他社会服务",
    "工程咨询服务Ⅱ": "其他社会服务",
    "工程咨询服务Ⅲ": "其他社会服务",
    "光学元件": "光学光电子",
    "被动元件": "元件",
    "化学制剂": "化学制药",
    "非白酒": "饮料制造",
    "电力设备": "电网设备",
    "火电设备": "其他电源设备",
    "环保": "环境治理",
    "光伏电池组件": "光伏设备",
    "激光设备": "专用设备",
    "综合环境治理": "环境治理",
    "综合电力设备商": "电网设备",
    "房地产综合服务": "房地产",
    "汽车综合服务": "汽车服务及其他",
    "旅游综合": "旅游及酒店",
    "综合乘用车": "汽车整车",
    "综合电商": "互联网电商",
    "综合包装": "包装印刷",
    "电能综合服务": "电力",
    "农业综合Ⅱ": "种植业与林业",
    "农业综合Ⅲ": "种植业与林业",
    "其他家电Ⅱ": "黑色家电",
    "其他家电Ⅲ": "黑色家电",
    "塑料": "塑料制品",
    "橡胶": "橡胶制品",
    "煤炭": "煤炭开采加工",
    "煤炭开采": "煤炭开采加工",
    "传媒": "文化传媒",
    "印刷": "包装印刷",
    "酒店": "旅游及酒店",
    "机场": "机场航运",
    "港口": "港口航运",
    "铁路运输": "公路铁路运输",
    "家纺": "服装家纺",
    "院线": "影视院线",
    "食品加工": "食品加工制造",
    "种植业": "种植业与林业",
    "林业Ⅱ": "种植业与林业",
    "林业Ⅲ": "种植业与林业",
    "电子化学品Ⅱ": "电子化学品",
    "电子化学品Ⅲ": "电子化学品",
}

# 禁止 (东财, 同花顺) 配对 — 自动/历史误匹配
FORBIDDEN_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("光学元件", "元件"),
        ("激光设备", "光伏设备"),
        ("非白酒", "白酒"),
        ("电力设备", "电力"),
        ("火电设备", "电网设备"),
        ("环保", "环保设备"),
        ("化学制剂", "化学制品"),
        ("其他家电Ⅱ", "其他电子"),
        ("其他家电Ⅲ", "其他电子"),
        ("光伏电池组件", "电池"),
        ("汽车综合服务", "综合"),
        ("旅游综合", "综合"),
        ("综合乘用车", "综合"),
        ("综合电商", "综合"),
        ("综合包装", "综合"),
        ("电能综合服务", "综合"),
        ("农业综合Ⅱ", "综合"),
        ("农业综合Ⅲ", "综合"),
        ("计算机", "计算机设备"),  # 东财「计算机」过宽，保留 jbxx 原名展开即可
    }
)

# 短名同花顺行业：不允许因「包含关系」自动挂靠（须走 MANUAL_HINTS）
SUBSTRING_GUARD_THS: frozenset[str] = frozenset(
    {
        "元件",
        "综合",
        "电力",
        "环保",
        "传媒",
        "燃气",
        "物流",
        "贸易",
        "电机",
        "电池",
        "游戏",
        "教育",
        "零售",
        "钢铁",
        "银行",
        "港口",
        "机场",
        "橡胶",
        "塑料",
        "印刷",
        "酒店",
        "煤炭",
        "纺织",
        "证券",
        "保险",
        "中药",
        "白酒",
        "养殖",
    }
)


def _remove_em_from_ths(aliases: dict[str, list[str]], em: str, ths: str) -> None:
    bucket = aliases.get(ths)
    if not bucket:
        return
    aliases[ths] = [x for x in bucket if x != em]


def _add_em_to_ths(aliases: dict[str, list[str]], em: str, ths: str) -> None:
    if not em or not ths:
        return
    bucket = aliases.setdefault(ths, [])
    if em not in bucket:
        bucket.append(em)
        bucket.sort()


def apply_alias_corrections(aliases: dict[str, list[str]]) -> dict[str, list[str]]:
    """应用禁止项与人工指向，返回新 aliases（原地修改并返回）。"""
    for em, wrong_ths in FORBIDDEN_PAIRS:
        _remove_em_from_ths(aliases, em, wrong_ths)

    for em, ths in MANUAL_HINTS.items():
        for key in list(aliases):
            _remove_em_from_ths(aliases, em, key)
        _add_em_to_ths(aliases, em, ths)
        if em == ths:
            continue
        # 异名映射时不重复保留在错误桶

    for key in list(aliases):
        aliases[key] = sorted(set(aliases[key]))
        if not aliases[key]:
            continue
    return aliases


def recompute_em_only(
    aliases: dict[str, list[str]],
    em_catalog_names: list[str],
    *,
    extra_em_only: list[str] | None = None,
) -> list[str]:
    """catalog 中东财名未出现在任一 aliases 值里 → em_only。"""
    mapped = {em for bucket in aliases.values() for em in bucket}
    em_only: set[str] = set(extra_em_only or [])
    for name in em_catalog_names:
        n = str(name).strip()
        if n and n not in mapped:
            em_only.add(n)
    # jbxx-only names in hints/forbidden might need em_only too
    for em in MANUAL_HINTS:
        if em not in mapped and em not in {x for x in em_catalog_names}:
            pass  # jbxx names handled by caller
    return sorted(em_only)


def substring_match_allowed(em: str, ths: str) -> bool:
    """东财名能否因包含关系匹配到同花顺名。"""
    em = em.strip()
    ths = ths.strip()
    if not em or not ths or em == ths:
        return em == ths

    if (em, ths) in FORBIDDEN_PAIRS:
        return False
    if em in MANUAL_HINTS and MANUAL_HINTS[em] != ths:
        return False

    if ths in SUBSTRING_GUARD_THS and ths in em and em != ths:
        # 光学元件 ⊃ 元件：ths 不在开头
        if em.find(ths) > 0:
            return False

    if em.startswith("其他") and ths.startswith("其他") and em != ths:
        return False

    if "综合" in em and ths == "综合" and em not in ("综合", "综合Ⅱ", "综合Ⅲ"):
        return False

    return True
