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
COLOR_RELEASE_POSTPONED = 0x95A5A6
COLOR_NEW_TITLE = 0x3498DB
COLOR_ERROR = 0xE74C3C

# Discordの上限。超えると400で恒久的に弾かれるため、送信前に丸める。
TITLE_MAX = 256
DESCRIPTION_MAX = 4096
FIELD_VALUE_MAX = 1024


class PermanentNotifyError(Exception):
    """再送しても必ず失敗する送信エラー（400等）。queueに戻さず破棄する。"""


def _truncate(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sanitize(embed: dict[str, Any]) -> dict[str, Any]:
    embed = dict(embed)
    if "title" in embed:
        embed["title"] = _truncate(embed["title"], TITLE_MAX)
    if "description" in embed:
        embed["description"] = _truncate(embed["description"], DESCRIPTION_MAX)
    if embed.get("fields"):
        embed["fields"] = [
            {**f, "value": _truncate(f.get("value", ""), FIELD_VALUE_MAX)} for f in embed["fields"]
        ]
    return embed


def send_embed(webhook_url: str, embed: dict[str, Any]) -> None:
    if not webhook_url:
        raise RuntimeError("Webhook URLが設定されていません（GitHub Secretsを確認してください）")

    payload = {"embeds": [_sanitize(embed)]}
    resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 429:
        try:
            retry_after = float(resp.json().get("retry_after", 1))
        except Exception:
            retry_after = 1.0
        time.sleep(retry_after + 0.5)
        resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)

    # 400番台（429を除く）はリクエスト内容自体の問題なので、何度送り直しても通らない。
    # queueに戻すとその1件が詰まって後続の通知が永久に出せなくなるため、破棄扱いにする。
    if 400 <= resp.status_code < 500 and resp.status_code != 429:
        raise PermanentNotifyError(f"Discordに拒否されました（HTTP {resp.status_code}）: {resp.text[:300]}")

    resp.raise_for_status()
    time.sleep(SEND_INTERVAL_SECONDS)


def build_news_embed(item) -> dict:
    embed: dict[str, Any] = {"title": item.title, "url": item.url, "color": COLOR_NEWS}
    if item.image_url:
        embed["image"] = {"url": item.image_url}
    return embed


def build_release_embed(item, kind: str, previous_date: str | None = None) -> dict:
    prefix = {"updated": "🔁 更新: ", "postponed": "⏳ 延期: "}.get(kind, "")
    fields = [{"name": "配信予定日", "value": item.date_text, "inline": True}]
    if kind == "postponed" and previous_date:
        fields.append({"name": "変更前", "value": previous_date, "inline": True})
    embed: dict[str, Any] = {
        "title": f"{prefix}{item.title}",
        "url": item.url,
        "fields": fields,
        "color": {"updated": COLOR_RELEASE_UPDATED, "postponed": COLOR_RELEASE_POSTPONED}.get(
            kind, COLOR_RELEASE_NEW
        ),
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
