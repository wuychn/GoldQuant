from datetime import datetime
from typing import Any

import httpx
from fastapi import HTTPException

from app.api.deps import SettingsDep


def list_to_dict(data_list: list[dict]) -> dict:
    """
    工具方法：
    列表中每个 dict 取【第一个值】作为 key
                【第二个值】作为 value
    最终合并成一个字典

    示例：
    输入：[{"item":"统计日期","value":"20240101"}]
    输出：{"统计日期":"20240101"}
    """
    result = {}

    if not isinstance(data_list, list):
        return result

    for item in data_list:
        if not isinstance(item, dict):
            continue

        # 取出所有值 -> 第一个是 key，第二个是 value
        values = list(item.values())
        if len(values) >= 2:
            key = values[0]
            value = values[1]
            result[key] = value

    return result

def format_percent(num):
    # 四舍五入保留2位小数
    res = round(num, 2)
    return f"{res}%"

async def call_ths_api(
        settings: SettingsDep,
        url: str
):
    headers = {
        "User-Agent": settings.THS_DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    client_kw: dict[str, Any] = {"timeout": settings.HTTP_CLIENT_TIMEOUT}
    if px := settings.httpx_proxy_url():
        client_kw["proxy"] = px
    try:
        async with httpx.AsyncClient(**client_kw) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

def today():
    return datetime.now().strftime("%Y%m%d")