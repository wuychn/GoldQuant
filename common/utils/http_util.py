from typing import Any

import httpx

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"

HTTP_CLIENT_TIMEOUT = 30


async def get_api(
        url: str
):
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    client_kw: dict[str, Any] = {"timeout": HTTP_CLIENT_TIMEOUT}
    try:
        async with httpx.AsyncClient(**client_kw) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        # common 层不依赖 fastapi：抛 RuntimeError，由上层（app endpoint）按需映射为 HTTP 502
        raise RuntimeError(f"HTTP 请求失败（502）: {exc}") from exc
