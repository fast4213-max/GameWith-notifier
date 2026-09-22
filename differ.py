"""前回stateとの差分検出。

「先頭からN件」ではなく既知IDとの集合差分で新着を判定するため、
一覧の途中に古い記事・新しいリリース予定が割り込んでも正しく検知できる。
"""
from __future__ import annotations

from typing import Any

from scraper import GameListItem, NewsItem


def diff_simple_list(
    items: list, known_ids: set[str]
) -> tuple[list, set[str]]:
    """ニュース・新作共通：既知IDにないものを新着として返す。"""
    new_items = [item for item in items if item.id not in known_ids]
    updated_known = known_ids | {item.id for item in items}
    return new_items, updated_known


def diff_release_schedule(
    items: list[GameListItem], games_state: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    リリース予定の差分判定。

    ルール:
      - 初出現かつ年月のみ判明 → 記録するが通知しない（pending）
      - 月日まで判明した時点（初回 or pendingからの更新） → 通知（kind="new"）
      - 通知済みの後に日付表記が変化（延期・前倒し） → 再通知（kind="updated"）

    戻り値: (to_notify, updated_games_state)
      to_notify の要素は {"item": GameListItem, "kind": "new" | "updated"}
    """
    to_notify: list[dict[str, Any]] = []
    updated_state: dict[str, dict[str, Any]] = dict(games_state)

    for item in items:
        if item.parsed_date is None:
            continue  # 想定外の日付表記。ログにのみ残し、次回の再取得に任せる

        existing = updated_state.get(item.id)

        if existing is None:
            if item.parsed_date.precision == "day":
                to_notify.append({"item": item, "kind": "new"})
                updated_state[item.id] = _make_record(item, notified=True)
            else:
                updated_state[item.id] = _make_record(item, notified=False)
            continue

        if not existing.get("notified"):
            if item.parsed_date.precision == "day":
                to_notify.append({"item": item, "kind": "new"})
                updated_state[item.id] = _make_record(item, notified=True)
            elif item.date_text != existing.get("date_text"):
                updated_state[item.id] = _make_record(item, notified=False)
            continue

        # 通知済み: 日付表記が変わっていれば延期・前倒しとして再通知
        if item.parsed_date.precision == "day" and item.date_text != existing.get("date_text"):
            to_notify.append({"item": item, "kind": "updated"})
            updated_state[item.id] = _make_record(item, notified=True)

    return to_notify, updated_state


def _make_record(item: GameListItem, notified: bool) -> dict[str, Any]:
    return {
        "title": item.title,
        "url": item.url,
        "image_url": item.image_url,
        "date_text": item.date_text,
        "precision": item.parsed_date.precision if item.parsed_date else None,
        "notified": notified,
    }
