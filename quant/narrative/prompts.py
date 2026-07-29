"""LLM 叙述 prompt 入口。

r3 活路径仅 news 模式（新闻摘要）；盘前/盘中/午间/晚间复盘 prompt 随 r2 LLM
复盘链路退役。买入/卖出文案改用因子归因（narrative/factor_phrases）与出场原因
（narrative/exit_phrases），由程序在推送 body 里直接拼装，不走 LLM 复盘。
"""

from __future__ import annotations

from datetime import datetime


LLM_OUTPUT_FORMAT = (
    "\n【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号；"
    "禁止出现「程序结论」「程序确认」「规则引擎」「全局门禁」「门禁」「标的池」"
    "「研判要点」「研判中的」「接口数据」「JSON 字段」"
    "「市场环境」「市场档位」「市场状态」「交易环境」「可参与交易」等系统或内部用语；"
    "行情强弱用「赚钱效应强/一般/差」，仓位用「仓位控制」及具体比例；"
    "概念与榜单用「当日涨幅」「资金流入」等自然说法。\n"
)


def prompt_news() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        f"当前日期：{today}。请对新闻去噪提炼。根据时间、重要性输出前50条要点和一段150字以内的综合解读，不要输出其他任何无关的内容。\n"
        + "以下是示例：\n"
        + "1. （要点）\n"
        + "2. （要点）\n"
        + "3. （要点）\n"
        + ".....\n"
        + "50. （新闻要点）\n"
        + "\n"
        + "综合解读：（新闻解读）\n"
        + LLM_OUTPUT_FORMAT
    )
