"""実行設定・環境変数の一元管理。"""
from __future__ import annotations

import os

# --- スクレイピング対象 ---
APPLI_URL = "https://gamewith.jp/appli"
NEWS_URL = "https://gamewith.jp/gamedb/news"

# --- Discord Webhook（GitHub Secretsから注入） ---
DISCORD_WEBHOOK_NEWS = os.environ.get("DISCORD_WEBHOOK_NEWS", "")
DISCORD_WEBHOOK_RELEASE_SCHEDULE = os.environ.get("DISCORD_WEBHOOK_RELEASE_SCHEDULE", "")
DISCORD_WEBHOOK_NEW_TITLES = os.environ.get("DISCORD_WEBHOOK_NEW_TITLES", "")

# --- state保存先 ---
STATE_DIR = "state"

# --- 通知・リトライ挙動 ---
MAX_NOTIFY_PER_RUN = 30  # 1チャンネル・1回の実行あたりの通知上限。10件ずつ1メッセージにまとめて送る。超過分はqueueへ
MAX_QUEUE_SIZE = 200  # queueの保持上限。Webhook設定ミス等で送信し続けられない場合の肥大化を防ぐ
FETCH_RETRIES = 3
FETCH_BACKOFF_SECONDS = (2, 4, 8)
# リトライ枯渇（一時的な通信エラー）が何回連続したらニュースチャンネルへエスカレーション通知するか
CONSECUTIVE_FAILURE_ALERT_THRESHOLD = 6
