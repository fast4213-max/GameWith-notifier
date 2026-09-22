"""Webhook設定の疎通確認用。3チャンネルへテスト通知を送るだけで、stateには触れない。"""
from __future__ import annotations

import config
import notifier


def main() -> None:
    notifier.send_embed(
        config.DISCORD_WEBHOOK_NEWS,
        {
            "title": "テスト通知（最新ニュース）",
            "description": "この通知が届けばWebhook設定は正常です。",
            "color": notifier.COLOR_NEWS,
        },
    )
    notifier.send_embed(
        config.DISCORD_WEBHOOK_RELEASE_SCHEDULE,
        {
            "title": "テスト通知（リリース予定）",
            "description": "この通知が届けばWebhook設定は正常です。",
            "color": notifier.COLOR_RELEASE_NEW,
        },
    )
    notifier.send_embed(
        config.DISCORD_WEBHOOK_NEW_TITLES,
        {
            "title": "テスト通知（新作）",
            "description": "この通知が届けばWebhook設定は正常です。",
            "color": notifier.COLOR_NEW_TITLE,
        },
    )
    print("3チャンネルへテスト通知を送信しました。")


if __name__ == "__main__":
    main()
