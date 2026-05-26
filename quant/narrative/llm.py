"""LLM 调用。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import requests

from app.core.config import get_settings
from quant.config import _RE_THINKING


def call_llm(
    system: str,
    user: str,
    *,
    max_tokens: int = 8000,
    retries: int = 3,
    temperature: float = 0.2,
) -> str:
    cfg = get_settings()
    api_key = (cfg.LLM_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY")
    url = f"{cfg.LLM_BASE_URL.rstrip('/')}/v1/messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": cfg.LLM_MODEL,
        "messages": [{"role": "user", "content": f"{system}\n\n{user}"}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=600)
            if resp.status_code == 529:
                time.sleep(5)
                continue
            resp.raise_for_status()
            for c in resp.json().get("content", []):
                if c.get("type") == "text":
                    text = _RE_THINKING.sub("", c["text"]).strip()
                    if text:
                        return text
            time.sleep(3)
        except requests.exceptions.RequestException as e:
            print(f"LLM 请求异常: {e}")
            time.sleep(5)
    raise RuntimeError("LLM 调用失败")
