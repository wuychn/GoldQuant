"""飞书推送。"""

from __future__ import annotations

import json

import requests

from quant.config import get_feishu_config


def get_token() -> str:
    app_id, app_secret, _ = get_feishu_config()
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError("获取飞书 token 失败")
    return result["tenant_access_token"]


def send_msg(content: str, token: str) -> None:
    _, _, user_id = get_feishu_config()
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {
        "receive_id": user_id,
        "msg_type": "text",
        "content": json.dumps({"text": content}),
    }
    resp = requests.post(url, headers=headers, json=data, timeout=600)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"飞书发送失败: {result}")
