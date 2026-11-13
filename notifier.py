"""Discord Webhookへの通知送信。タイトルにリンクを埋め込み、本文にURLは出さない。"""
from __future__ import annotations

import re
import time
from typing import Any

import requests

REQUEST_TIMEOUT = 15
SEND_INTERVAL_SECONDS = 1.2  # 連続POST時の簡易レート制限対策
MAX_429_RETRIES = 3

COLOR_NEWS = 0x2ECC71
COLOR_NEWS_SCHEDULE_CHANGE = 0xE67E22
COLOR_RELEASE_NEW = 0xF39C12
COLOR_RELEASE_UPDATED = 0x9B59B6
COLOR_RELEASE_POSTPONED = 0x95A5A6
COLOR_NEW_TITLE = 0x3498DB
COLOR_ERROR = 0xE74C3C

# Discordの上限。超えると400で恒久的に弾かれるため、送信前に丸める。
TITLE_MAX = 256
DESCRIPTION_MAX = 4096
FIELD_VALUE_MAX = 1024
EMBEDS_PER_MESSAGE = 10  # 1メッセージに載せられるembed数の上限
EMBEDS_TOTAL_CHARS_MAX = 6000  # 1メッセージ内の全embedの合計文字数上限


# 延期・配信時期変更のニュースを目立たせるためのタイトル判定。
# 「配信日決定」のような前向きな告知は対象外にするため、変更・見直し系の語に限定する
SCHEDULE_CHANGE_PATTERN = re.compile(
    r"延期|(配信|発売|リリース|サービス開始)(時期|日|日程|予定日?)(が|を)?(変更|見直|再調整)"
)


class PermanentNotifyError(Exception):
    """再送しても必ず失敗する送信エラー（400等）。queueに戻さず破棄する。"""


class WebhookConfigError(Exception):
    """Webhook自体が使えない（未設定・削除済み・トークン不正）。

    通知の内容ではなく設定の問題なので、破棄せずqueueに戻して設定の修正を待つ。
    放置されないようActionsの実行を失敗扱いにするため、他の送信エラーと区別する。
    """


class TransientNotifyError(Exception):
    """一時的な送信エラー（通信障害・5xx・429リトライ枯渇）。queueに戻して次回再送する。"""


# Webhookが削除された／トークンが不正なときにDiscordが返すステータス
WEBHOOK_CONFIG_ERROR_STATUSES = (401, 403, 404)

_WEBHOOK_TOKEN_PATTERN = re.compile(r"(webhooks/\d+/)[A-Za-z0-9_.\-]+")


def redact(text: str) -> str:
    """Webhookのトークン部分を伏せる。

    requestsの通信エラーのメッセージには `/api/webhooks/<ID>/<トークン>` がパスだけで入る。
    GitHub ActionsはSecretsの値（URL全体）と完全一致した部分しかマスクしないため、
    そのままログに出すと公開リポジトリの実行ログからトークンが読めてしまう。
    """
    return _WEBHOOK_TOKEN_PATTERN.sub(r"\1***", str(text))


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


def _embed_chars(embed: dict[str, Any]) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    for f in embed.get("fields") or []:
        total += len(str(f.get("name", ""))) + len(str(f.get("value", "")))
    total += len((embed.get("footer") or {}).get("text", ""))
    total += len((embed.get("author") or {}).get("name", ""))
    return total


def chunk_embeds(embeds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """1メッセージ10件・合計6000文字の上限に収まるようにembedをまとめる。"""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for embed in embeds:
        size = _embed_chars(_sanitize(embed))
        if current and (len(current) >= EMBEDS_PER_MESSAGE or chars + size > EMBEDS_TOTAL_CHARS_MAX):
            chunks.append(current)
            current, chars = [], 0
        current.append(embed)
        chars += size
    if current:
        chunks.append(current)
    return chunks


def _retry_after(resp: requests.Response) -> float:
    try:
        return float(resp.json().get("retry_after", 1))
    except Exception:
        return 1.0


def _wait_for_bucket(resp: requests.Response) -> None:
    """残り回数が0ならリセットまで待ち、次のPOSTで429を踏まないようにする。"""
    wait = SEND_INTERVAL_SECONDS
    try:
        if str(resp.headers.get("X-RateLimit-Remaining")) == "0":
            wait = max(wait, float(resp.headers.get("X-RateLimit-Reset-After")) + 0.2)
    except (TypeError, ValueError, AttributeError):
        pass
    time.sleep(wait)


def _post(webhook_url: str, payload: dict[str, Any]) -> requests.Response:
    try:
        return requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        # 元の例外はメッセージにトークンを含むため、連鎖させず（from None）伏せた内容だけ残す
        raise TransientNotifyError(f"Discordへの送信で通信エラー: {redact(exc)}") from None


def send_embeds(webhook_url: str, embeds: list[dict[str, Any]]) -> None:
    """embedをまとめて1メッセージで送る（最大10件）。"""
    if not webhook_url:
        raise WebhookConfigError("Webhook URLが設定されていません（GitHub Secretsを確認してください）")
    if not 1 <= len(embeds) <= EMBEDS_PER_MESSAGE:
        raise ValueError(f"1メッセージのembed数は1〜{EMBEDS_PER_MESSAGE}件です: {len(embeds)}")

    payload = {"embeds": [_sanitize(e) for e in embeds]}
    resp = _post(webhook_url, payload)
    for _ in range(MAX_429_RETRIES):
        if resp.status_code != 429:
            break
        time.sleep(_retry_after(resp) + 0.5)
        resp = _post(webhook_url, payload)

    body = redact(str(resp.text)[:300])
    # 401/403/404はWebhookの削除・トークン不正。内容を変えても通らないが、設定を直せば送れるので破棄しない
    if resp.status_code in WEBHOOK_CONFIG_ERROR_STATUSES:
        raise WebhookConfigError(
            f"Webhookが無効です（HTTP {resp.status_code}、削除またはURL誤りの可能性。GitHub Secretsを確認してください）: {body}"
        )
    # それ以外の400番台（429を除く）はリクエスト内容自体の問題なので、何度送り直しても通らない。
    # queueに戻すとその通知が詰まって後続が永久に出せなくなるため、破棄扱いにする。
    if 400 <= resp.status_code < 500 and resp.status_code != 429:
        raise PermanentNotifyError(f"Discordに拒否されました（HTTP {resp.status_code}）: {body}")
    # raise_for_status()はメッセージにURL全体を含むため使わない
    if resp.status_code >= 400:
        raise TransientNotifyError(f"Discordへの送信に失敗しました（HTTP {resp.status_code}）: {body}")
    _wait_for_bucket(resp)


def send_embed(webhook_url: str, embed: dict[str, Any]) -> None:
    send_embeds(webhook_url, [embed])


def is_schedule_change_news(title: str) -> bool:
    return bool(SCHEDULE_CHANGE_PATTERN.search(title))


def build_news_embed(item) -> dict:
    if is_schedule_change_news(item.title):
        embed: dict[str, Any] = {
            "title": f"⏳ 配信日変更: {item.title}",
            "url": item.url,
            "color": COLOR_NEWS_SCHEDULE_CHANGE,
        }
    else:
        embed = {"title": item.title, "url": item.url, "color": COLOR_NEWS}
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
