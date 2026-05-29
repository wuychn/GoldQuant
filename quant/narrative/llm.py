"""LLM 调用（统一走 ``app.utils.llm_client``）。"""

from __future__ import annotations

import time

import requests

from app.core.config import get_settings
from app.utils.llm_client import (
    extract_text_from_llm_response,
    format_llm_request_error,
    post_llm_chat,
)
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

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            data = post_llm_chat(
                api_key=api_key,
                base_url=cfg.LLM_BASE_URL,
                model=cfg.LLM_MODEL,
                system=system,
                user=user,
                max_tokens=max_tokens,
                temperature=temperature,
                settings=cfg,
            )
            text = extract_text_from_llm_response(data, api_format=cfg.LLM_API_FORMAT)
            if text:
                return _RE_THINKING.sub("", text).strip()
            last_err = RuntimeError("LLM 响应无文本内容")
            time.sleep(3)
        except requests.HTTPError as e:
            last_err = e
            if e.response is not None and e.response.status_code == 529:
                time.sleep(5)
                continue
            print(format_llm_request_error(e, base_url=cfg.LLM_BASE_URL, api_format=cfg.LLM_API_FORMAT))
            time.sleep(5)
        except Exception as e:
            last_err = e
            if getattr(getattr(e, "response", None), "status_code", None) == 529:
                time.sleep(5)
                continue
            print(format_llm_request_error(e, base_url=cfg.LLM_BASE_URL, api_format=cfg.LLM_API_FORMAT))
            time.sleep(5)

    raise RuntimeError(f"LLM 调用失败: {last_err}")
