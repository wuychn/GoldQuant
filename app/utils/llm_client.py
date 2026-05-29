"""LLM HTTP 客户端：OpenAI ``/chat/completions``（默认，兼容 LangChain/火山方舟）与 Anthropic ``/v1/messages``。"""

from __future__ import annotations

import logging
from typing import Any, Literal

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

LlmApiFormat = Literal["openai", "anthropic"]


def normalize_llm_api_format(fmt: str) -> LlmApiFormat:
    f = (fmt or "openai").strip().lower()
    if f in ("anthropic", "claude", "messages"):
        return "anthropic"
    return "openai"


def llm_api_url(base_url: str, api_format: str) -> str:
    """根据格式拼接路径；``base_url`` 勿含 ``chat/completions`` 或 ``v1/messages``。"""
    base = base_url.rstrip("/")
    fmt = normalize_llm_api_format(api_format)
    suffix = "/v1/messages" if fmt == "anthropic" else "/chat/completions"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def llm_request_proxies(settings: Settings) -> dict[str, str] | None:
    if not settings.LLM_USE_APP_PROXY or not settings.PROXY_ENABLED:
        return None
    px = settings.httpx_proxy_url()
    if not px:
        return None
    return {"http": px, "https": px}


def _build_session(settings: Settings) -> requests.Session:
    session = requests.Session()
    session.trust_env = settings.LLM_TRUST_ENV_PROXY
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504, 529),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _build_headers(api_key: str, api_format: LlmApiFormat) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if api_format == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _build_payload(
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    api_format: LlmApiFormat,
) -> dict[str, Any]:
    if api_format == "anthropic":
        return {
            "model": model,
            "messages": [{"role": "user", "content": f"{system}\n\n{user}".strip()}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    messages: list[dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": user})
    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def post_llm_chat(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 8000,
    temperature: float = 0.2,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    api_format = normalize_llm_api_format(cfg.LLM_API_FORMAT)
    url = llm_api_url(base_url, api_format)
    payload = _build_payload(
        model=model,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        api_format=api_format,
    )
    timeout = (30, int(cfg.LLM_TIMEOUT_SEC))
    session = _build_session(cfg)
    resp = session.post(
        url,
        headers=_build_headers(api_key, api_format),
        json=payload,
        timeout=timeout,
        proxies=llm_request_proxies(cfg),
        verify=cfg.LLM_VERIFY_SSL,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("LLM 响应不是 JSON 对象")
    return data


# 兼容旧调用名
post_llm_messages = post_llm_chat


def extract_text_from_llm_response(
    data: dict[str, Any],
    *,
    api_format: str | None = None,
) -> str | None:
    fmt = normalize_llm_api_format(api_format or get_settings().LLM_API_FORMAT)
    if fmt == "anthropic":
        for c in data.get("content", []):
            if isinstance(c, dict) and c.get("type") == "text":
                text = str(c.get("text") or "").strip()
                if text:
                    return text
        return None
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    msg = choices[0].get("message") or {}
    if isinstance(msg, dict):
        text = str(msg.get("content") or "").strip()
        if text:
            return text
    return None


extract_text_from_messages_response = extract_text_from_llm_response


def format_llm_request_error(exc: Exception, *, base_url: str, api_format: str | None = None) -> str:
    fmt = normalize_llm_api_format(api_format or get_settings().LLM_API_FORMAT)
    path = "/chat/completions" if fmt == "openai" else "/v1/messages"
    host = base_url.split("//", 1)[-1].split("/", 1)[0]
    hint = (
        f"目标: {host}{path}（格式={fmt}）。常见原因："
        "① 系统代理干扰 HTTPS（GOLDQUANT_LLM_TRUST_ENV_PROXY=false）；"
        "② LLM_BASE_URL 勿含 chat/completions 或 v1/messages；"
        "③ 火山/LangChain 用 LLM_API_FORMAT=openai；MiniMax Anthropic 兼容用 anthropic；"
        "④ 自签证书设 GOLDQUANT_LLM_VERIFY_SSL=false。"
    )
    return f"LLM 请求异常: {exc}。{hint}"
