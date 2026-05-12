"""API 数据获取。"""

import requests

from quant.config import BASE_URL


def fetch_data(endpoint: str) -> dict:
    resp = requests.get(f"{BASE_URL}{endpoint}", timeout=600)
    resp.raise_for_status()
    return resp.json()


def fetch_news():
    return fetch_data("/api/v1/quant/market/news")


def fetch_pre_market():
    return fetch_data("/api/v1/quant/market/pre_market")


def fetch_during_market():
    return fetch_data("/api/v1/quant/market/during_market")


def fetch_post_market():
    return fetch_data("/api/v1/quant/market/post_market")
