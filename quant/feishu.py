"""Feishu text-message delivery."""

from __future__ import annotations

import json

import requests

from app.core.config import get_settings


def _required(value: str | None, name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise RuntimeError(f"未配置 {name}，请在 .env 中设置 FEISHU_* 或 GOLDQUANT_FEISHU_*")
    return text


def get_token() -> str:
    settings = get_settings()
    app_id = _required(settings.FEISHU_APP_ID, "FEISHU_APP_ID")
    app_secret = _required(settings.FEISHU_APP_SECRET, "FEISHU_APP_SECRET")
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError("获取token失败")
    return result["tenant_access_token"]


def send_text(content: str, token: str | None = None) -> None:
    user_id = _required(get_settings().FEISHU_USER_ID, "FEISHU_USER_ID")
    access_token = token or get_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    data = {"receive_id": user_id, "msg_type": "text", "content": json.dumps({"text": content})}
    resp = requests.post(url, headers=headers, json=data, timeout=600)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"发送失败: {result}")
