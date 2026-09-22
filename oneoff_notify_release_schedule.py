"""【使い切り・一時スクリプト】

init-notifyで「通知済み」扱いにしてしまった、月日確定済みのリリース予定を
1回だけ手動で通知するためのもの。Discordで届いたことを確認できたら
このファイルとworkflow内の一時ステップは削除してよい。
"""
from __future__ import annotations

import config
import notifier
import state_manager


def main() -> None:
    state = state_manager.load_json("state/release_schedule.json", {"games": {}})
    games = state.get("games", {})
    day_precision = {gid: rec for gid, rec in games.items() if rec.get("precision") == "day"}

    print(f"送信対象: {len(day_precision)}件")
    for gid, rec in sorted(day_precision.items(), key=lambda kv: kv[1]["date_text"]):
        embed = {
            "title": rec["title"],
            "url": rec["url"],
            "fields": [{"name": "配信予定日", "value": rec["date_text"], "inline": True}],
            "color": notifier.COLOR_RELEASE_NEW,
        }
        if rec.get("image_url"):
            embed["image"] = {"url": rec["image_url"]}
        notifier.send_embed(config.DISCORD_WEBHOOK_RELEASE_SCHEDULE, embed)
        print("sent:", rec["date_text"], rec["title"])

    print("完了")


if __name__ == "__main__":
    main()
