from typing import Any

import httpx
from fastapi import HTTPException

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
        raise HTTPException(status_code=502, detail=str(exc)) from exc
