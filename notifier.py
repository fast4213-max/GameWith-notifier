"""Discord Webhookへの通知送信。タイトルにリンクを埋め込み、本文にURLは出さない。"""
from __future__ import annotations

import time
from typing import Any

import requests

REQUEST_TIMEOUT = 15
SEND_INTERVAL_SECONDS = 1.2  # 連続POST時の簡易レート制限対策

COLOR_NEWS = 0x2ECC71
COLOR_RELEASE_NEW = 0xF39C12
COLOR_RELEASE_UPDATED = 0x9B59B6
COLOR_NEW_TITLE = 0x3498DB
COLOR_ERROR = 0xE74C3C


def send_embed(webhook_url: str, embed: dict[str, Any]) -> None:
    if not webhook_url:
        raise RuntimeError("Webhook URLが設定されていません（GitHub Secretsを確認してください）")

    resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 429:
        try:
            retry_after = float(resp.json().get("retry_after", 1))
        except Exception:
            retry_after = 1.0
        time.sleep(retry_after + 0.5)
        resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=REQUEST_TIMEOUT)

    resp.raise_for_status()
    time.sleep(SEND_INTERVAL_SECONDS)


def build_news_embed(item) -> dict:
    embed: dict[str, Any] = {"title": item.title, "url": item.url, "color": COLOR_NEWS}
    if item.image_url:
        embed["image"] = {"url": item.image_url}
    return embed


def build_release_embed(item, kind: str) -> dict:
    prefix = "🔁 更新: " if kind == "updated" else ""
    embed: dict[str, Any] = {
        "title": f"{prefix}{item.title}",
        "url": item.url,
        "fields": [{"name": "配信予定日", "value": item.date_text, "inline": True}],
        "color": COLOR_RELEASE_UPDATED if kind == "updated" else COLOR_RELEASE_NEW,
    }
    if item.image_url:
        embed["image"] = {"url": item.image_url}
    return embed


def build_new_title_embed(item) -> dict:
    embed: dict[str, Any] = {
        "title": item.title,
        "url": item.url,
        "fields": [{"name": "配信日", "value": item.date_text, "inline": True}],
        "color": COLOR_NEW_TITLE,
    }
    if item.image_url:
        embed["image"] = {"url": item.image_url}
    return embed


def build_error_embed(channel_label: str, message: str) -> dict:
    return {"title": f"⚠️ 取得エラー: {channel_label}", "description": message, "color": COLOR_ERROR}
