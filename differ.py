"""前回stateとの差分検出。

「先頭からN件」ではなく既知IDとの集合差分で新着を判定するため、
一覧の途中に古い記事・新しいリリース予定が割り込んでも正しく検知できる。
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from date_utils import ParsedReleaseDate, parse_release_date, today_jst
from scraper import GameListItem

logger = logging.getLogger("gamewith-notifier.differ")

# 既知IDの保持上限。stateファイルの無限肥大を防ぐ。
# 1ページあたり20件程度しか掲載されないため、この件数を残しておけば
# 掲載中の記事が「未知」に戻って再通知される心配はない。
KNOWN_IDS_LIMIT = 2000

# リリース予定から消えた項目を何日保持するか（配信済み・掲載終了分の掃除）
GAME_RETENTION_DAYS = 120


def _is_known(item, known_ids: set[str]) -> bool:
    if item.id in known_ids:
        return True
    # 旧方式のID（URL末尾のみ）で記録されている分を既知として扱う互換処理。
    # これがないとID方式の変更時に掲載中の記事が一斉に再通知されてしまう。
    legacy_id = getattr(item, "legacy_id", None)
    return bool(legacy_id) and legacy_id in known_ids


def diff_simple_list(
    items: list, known_ids: set[str], previous_known: list[str] | None = None
) -> tuple[list, list[str]]:
    """ニュース・新作共通：既知IDにないものを新着として返す。

    戻り値のIDリストは「最近見た順」に並べ、KNOWN_IDS_LIMIT件で打ち切る。
    """
    # 同じ項目が一覧に2回載っていると、二重通知・known_idsの重複が起きるため先に除く
    unique_items: list = []
    seen: set[str] = set()
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            unique_items.append(item)
    new_items = [item for item in unique_items if not _is_known(item, known_ids)]

    current_ids = [item.id for item in unique_items]
    current_set = set(current_ids)
    older = [i for i in (previous_known or sorted(known_ids)) if i not in current_set]
    updated_known = (current_ids + older)[:KNOWN_IDS_LIMIT]
    return new_items, updated_known


def drop_legacy_news_ids(known_ids: list[str], current_ids: set[str]) -> list[str]:
    """一覧から外れた記事の旧方式ニュースID（URL末尾のみ＝"/"を含まない）を除く。

    掲載中の記事は diff_simple_list が新方式で記録し直すため、ここで消えるのは
    一覧から既に外れた記事の旧IDだけ。残しておくと番号の衝突で新着を取りこぼす。
    （gamewith.jp直下1階層のURLは新方式でも"/"を含まないため、掲載中のIDは残す）
    """
    return [i for i in known_ids if "/" in i or i in current_ids]


def diff_release_schedule(
    items: list[GameListItem], games_state: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    リリース予定の差分判定。

    ルール:
      - 初出現かつ年月のみ判明 → 記録するが通知しない（pending）
      - 月日まで判明した時点（初回 or pendingからの更新） → 通知（kind="new"）
      - 通知済みの後に日付表記が変化（延期・前倒し） → 再通知（kind="updated"）
        「9月22日 → 26年12月」のように精度が粗くなる延期も再通知の対象。
      - 通知済みの後に「未定」等（数字を含まない解析不能表記）へ変化 → 延期通知（kind="postponed"）

    戻り値: (to_notify, updated_games_state)
      to_notify の要素は {"item": GameListItem, "kind": "new" | "updated" | "postponed"}
      postponed の場合は "previous"（変更前の日付表記）も含む
    """
    to_notify: list[dict[str, Any]] = []
    updated_state: dict[str, dict[str, Any]] = dict(games_state)
    today = today_jst()

    for item in items:
        existing = updated_state.get(item.id)

        if item.parsed_date is None:
            if existing is None:
                continue  # 想定外の日付表記。scraper側でログに残し、次回の再取得に任せる
            # 通知済みの項目が「未定」等になった＝延期として通知する。
            # 数字を含む表記は未対応の日付フォーマットの可能性が高く、延期と決めつけると
            # 誤通知になるため、掲載中であることだけ記録する（放置すると掲載中なのに
            # GAME_RETENTION_DAYS経過で削除され、日付が戻った時に新規扱いで再通知される）
            if (
                existing.get("notified")
                and item.date_text != existing.get("date_text")
                and not re.search(r"\d", item.date_text)
            ):
                to_notify.append(
                    {"item": item, "kind": "postponed", "previous": existing.get("date_text")}
                )
                updated_state[item.id] = _make_record(item, notified=True, today=today)
            else:
                updated_state[item.id] = {**existing, "last_seen": today.isoformat()}
            continue

        if existing is None:
            notify = item.parsed_date.precision == "day"
            if notify:
                to_notify.append({"item": item, "kind": "new"})
            updated_state[item.id] = _make_record(item, notified=notify, today=today)
            continue

        if not existing.get("notified"):
            if item.parsed_date.precision == "day":
                to_notify.append({"item": item, "kind": "new"})
                updated_state[item.id] = _make_record(item, notified=True, today=today)
            else:
                # 年月のみのまま。表記が変わっていても通知はせず記録だけ更新する
                updated_state[item.id] = _make_record(item, notified=False, today=today)
            continue

        # 通知済み: 日付が変わっていれば延期・前倒しとして再通知。
        # 「9月24日」→「9月24日（水）」のような表記ゆれだけの変化では再通知しない
        if _date_changed(existing.get("date_text"), item.parsed_date, today):
            to_notify.append({"item": item, "kind": "updated"})
        updated_state[item.id] = _make_record(item, notified=True, today=today)

    return to_notify, _prune_games(updated_state, today)


def _date_changed(previous_text: str | None, current: ParsedReleaseDate, today: date) -> bool:
    previous = parse_release_date(previous_text, today) if previous_text else None
    if previous is None:
        return previous_text != current.text
    return (previous.precision, previous.approx_date) != (current.precision, current.approx_date)


def _make_record(item: GameListItem, notified: bool, today=None) -> dict[str, Any]:
    return {
        "title": item.title,
        "url": item.url,
        "image_url": item.image_url,
        "date_text": item.date_text,
        "precision": item.parsed_date.precision if item.parsed_date else None,
        "notified": notified,
        "last_seen": (today or today_jst()).isoformat(),
    }


def _prune_games(games: dict[str, dict[str, Any]], today) -> dict[str, dict[str, Any]]:
    """掲載が終わって一定期間たった項目を落とす（stateの無限肥大を防ぐ）。"""
    kept: dict[str, dict[str, Any]] = {}
    for game_id, record in games.items():
        last_seen = record.get("last_seen")
        if last_seen is None:
            # last_seen導入前の既存レコード。今回の実行時点を起点として保持する
            kept[game_id] = {**record, "last_seen": today.isoformat()}
            continue
        try:
            age = (today - date.fromisoformat(last_seen)).days
        except ValueError:
            kept[game_id] = {**record, "last_seen": today.isoformat()}
            continue
        if age <= GAME_RETENTION_DAYS:
            kept[game_id] = record
        else:
            logger.info("リリース予定stateから削除（%d日間掲載なし）: %s", age, record.get("title"))
    return kept
