"""LLM client utilities used by the assistant pipeline."""

from __future__ import annotations

import os
import re
import time

import requests

from app.core.config import get_settings


DEFAULT_OUTPUT_FORMAT = "\n【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号。\n"

_RE_THINKING = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL)


def parallel_workers() -> int:
    return max(2, min(8, (os.cpu_count() or 4)))


def call_llm(
    system: str,
    user: str,
    max_tokens: int = 16000,
    retries: int = 3,
    *,
    temperature: float | None = None,
) -> str:
    cfg = get_settings()
    api_key = (cfg.LLM_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY，请在 .env 中设置")
    base = cfg.LLM_BASE_URL.rstrip("/")
    url = f"{base}/v1/messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    temp = 0.3 if temperature is None else float(temperature)
    payload = {
        "model": cfg.LLM_MODEL,
        "messages": [{"role": "user", "content": f"{system}\n\n{user}"}],
        "max_tokens": max_tokens,
        "temperature": temp,
    }
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=600)
            if resp.status_code == 529:
                print(f"LLM限流，重试({attempt + 1}/{retries})...")
                time.sleep(5)
                continue
            resp.raise_for_status()
            result = resp.json()
            for content in result.get("content", []):
                if content.get("type") == "text":
                    text = _RE_THINKING.sub("", content["text"]).strip()
                    if text:
                        return text
            stop = result.get("stop_reason", "")
            if attempt < retries - 1:
                print(f"LLM输出无文本(stop_reason={stop})，重试({attempt + 1}/{retries})...")
                time.sleep(3)
                continue
            print(f"LLM响应无text内容 stop_reason={stop} model={cfg.LLM_MODEL}")
            raise RuntimeError(f"LLM响应无有效文本(stop_reason={stop})")
        except requests.exceptions.RequestException as exc:
            print(f"LLM请求异常: {exc}")
            if attempt < retries - 1:
                time.sleep(5)
    raise RuntimeError("LLM调用失败")
