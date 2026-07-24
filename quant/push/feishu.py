"""飞书 Open API。"""

from __future__ import annotations

import json
import logging

import requests

from quant.config import get_feishu_config

logger = logging.getLogger(__name__)


def get_token() -> str:
    app_id, app_secret, _ = get_feishu_config()
    if not app_id or not app_secret:
        raise RuntimeError("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {data}")
    return str(data["tenant_access_token"])


def send_msg(content: str, token: str) -> None:
    _, _, user_id = get_feishu_config()
    if not user_id:
        raise RuntimeError("未配置 FEISHU_USER_ID")
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "open_id"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "receive_id": user_id,
            "msg_type": "text",
            "content": json.dumps({"text": content[:28000]}, ensure_ascii=False),
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书发送失败: {data}")
    logger.info("飞书推送成功 len=%s", len(content))
