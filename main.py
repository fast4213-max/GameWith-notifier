"""通常実行エントリポイント：3チャンネル分を取得→差分検出→通知→state更新。

エラーの扱い:
  - 通信エラー（リトライ3回枯渇）→ 一時的な障害とみなし通知はせず静かにスキップ、
    次回実行（1時間後）に任せる。ただし連続失敗がCONSECUTIVE_FAILURE_ALERT_THRESHOLD回に
    達するたびにニュースチャンネルへエスカレーション通知する。
  - ページ構造エラー（想定した要素が見つからない）→ リトライしても直らないため、
    その場でニュースチャンネルへ通知する。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

import config
import differ
import notifier
import scraper
import state_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gamewith-notifier")


def _state_path(name: str) -> str:
    return os.path.join(config.STATE_DIR, name)


def _notify_error_to_news(embed: dict[str, Any]) -> None:
    try:
        notifier.send_embed(config.DISCORD_WEBHOOK_NEWS, embed)
    except Exception:
        logger.exception("エラー通知の送信自体にも失敗しました")


def _handle_fetch_error(state: dict[str, Any], channel_label: str, exc: Exception) -> None:
    state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
    logger.warning("%s: 取得失敗（リトライ枯渇、次回実行に任せます）: %s", channel_label, exc)
    n = state["consecutive_errors"]
    if n > 0 and n % config.CONSECUTIVE_FAILURE_ALERT_THRESHOLD == 0:
        _notify_error_to_news(
            notifier.build_error_embed(
                channel_label, f"{n}回連続で取得に失敗しています（一時的な通信障害の可能性）: {exc}"
            )
        )


def _handle_parse_error(channel_label: str, exc: Exception) -> None:
    logger.error("%s: ページ構造エラー: %s", channel_label, exc)
    _notify_error_to_news(
        notifier.build_error_embed(channel_label, f"ページ構造が変わった可能性があります（要コード確認）: {exc}")
    )


def _drain_and_notify(
    queue: list[dict], new_embeds: list[dict], webhook_url: str, channel_label: str
) -> list[dict]:
    """queueに積まれた分を優先して送信し、上限超過分は新たにqueueへ積む。"""
    to_send = queue + new_embeds
    send_now = to_send[: config.MAX_NOTIFY_PER_RUN]
    carry_over = to_send[config.MAX_NOTIFY_PER_RUN :]

    sent = 0
    for embed in send_now:
        try:
            notifier.send_embed(webhook_url, embed)
            sent += 1
        except Exception:
            logger.exception("%s: 通知送信に失敗しました。残りをqueueに戻します", channel_label)
            carry_over = send_now[sent:] + carry_over
            break

    if len(to_send) > config.MAX_NOTIFY_PER_RUN and sent == len(send_now):
        logger.info(
            "%s: 新着が上限(%d件)を超えたため%d件を次回に持ち越します",
            channel_label,
            config.MAX_NOTIFY_PER_RUN,
            len(carry_over),
        )
    logger.info("%s: %d件通知（queue残り%d件）", channel_label, sent, len(carry_over))
    return carry_over


def run_news() -> None:
    channel_label = "最新ニュース"
    path = _state_path("news.json")
    state = state_manager.load_json(path, {"known_ids": [], "queue": [], "consecutive_errors": 0})
    known_ids = set(state.get("known_ids", []))

    try:
        html = scraper.fetch_html(config.NEWS_URL, config.FETCH_RETRIES, config.FETCH_BACKOFF_SECONDS)
        items = scraper.scrape_news(html)
    except scraper.FetchError as exc:
        _handle_fetch_error(state, channel_label, exc)
        state_manager.save_json(path, state)
        return
    except scraper.ParseError as exc:
        _handle_parse_error(channel_label, exc)
        state_manager.save_json(path, state)
        return

    state["consecutive_errors"] = 0
    new_items, updated_ids = differ.diff_simple_list(items, known_ids)
    # 一覧は新しい順のため、古い順に通知した方がDiscord上でも時系列が自然になる
    new_embeds = [notifier.build_news_embed(item) for item in reversed(new_items)]

    state["queue"] = _drain_and_notify(
        state.get("queue", []), new_embeds, config.DISCORD_WEBHOOK_NEWS, channel_label
    )
    state["known_ids"] = sorted(updated_ids)
    state_manager.save_json(path, state)


def run_new_titles() -> None:
    channel_label = "新作"
    path = _state_path("new_titles.json")
    state = state_manager.load_json(path, {"known_ids": [], "queue": [], "consecutive_errors": 0})
    known_ids = set(state.get("known_ids", []))

    try:
        html = scraper.fetch_html(config.APPLI_URL, config.FETCH_RETRIES, config.FETCH_BACKOFF_SECONDS)
        items = scraper.scrape_new_titles(html)
    except scraper.FetchError as exc:
        _handle_fetch_error(state, channel_label, exc)
        state_manager.save_json(path, state)
        return
    except scraper.ParseError as exc:
        _handle_parse_error(channel_label, exc)
        state_manager.save_json(path, state)
        return

    state["consecutive_errors"] = 0
    new_items, updated_ids = differ.diff_simple_list(items, known_ids)
    new_embeds = [notifier.build_new_title_embed(item) for item in reversed(new_items)]

    state["queue"] = _drain_and_notify(
        state.get("queue", []), new_embeds, config.DISCORD_WEBHOOK_NEW_TITLES, channel_label
    )
    state["known_ids"] = sorted(updated_ids)
    state_manager.save_json(path, state)


def run_release_schedule() -> None:
    channel_label = "リリース予定"
    path = _state_path("release_schedule.json")
    state = state_manager.load_json(path, {"games": {}, "queue": [], "consecutive_errors": 0})

    try:
        html = scraper.fetch_html(config.APPLI_URL, config.FETCH_RETRIES, config.FETCH_BACKOFF_SECONDS)
        items = scraper.scrape_release_schedule(html)
    except scraper.FetchError as exc:
        _handle_fetch_error(state, channel_label, exc)
        state_manager.save_json(path, state)
        return
    except scraper.ParseError as exc:
        _handle_parse_error(channel_label, exc)
        state_manager.save_json(path, state)
        return

    state["consecutive_errors"] = 0
    to_notify, updated_games = differ.diff_release_schedule(items, state.get("games", {}))
    new_embeds = [notifier.build_release_embed(entry["item"], entry["kind"]) for entry in to_notify]

    state["queue"] = _drain_and_notify(
        state.get("queue", []), new_embeds, config.DISCORD_WEBHOOK_RELEASE_SCHEDULE, channel_label
    )
    state["games"] = updated_games
    state_manager.save_json(path, state)


def main() -> int:
    error_count = 0
    for label, fn in (
        ("最新ニュース", run_news),
        ("新作", run_new_titles),
        ("リリース予定", run_release_schedule),
    ):
        try:
            fn()
        except Exception as exc:  # 想定外のバグ。握りつぶさず必ず可視化する
            error_count += 1
            logger.exception("%sの処理中に想定外のエラーが発生しました", label)
            _notify_error_to_news(
                notifier.build_error_embed(label, f"想定外のエラーが発生しました。ログを確認してください: {exc}")
            )

    logger.info("実行完了（想定外エラー%d件）", error_count)
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
