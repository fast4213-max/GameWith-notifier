"""初回セットアップ用: 現在の掲載内容を「既読」として記録するだけで通知は送らない。

これを実行せずに main.py を最初に動かすと、既存の掲載物が全て「新着」扱いされ
大量通知が飛ぶため、必ず最初に一度だけこちらを実行すること。
"""
from __future__ import annotations

import logging
import os

import config
import scraper
import state_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gamewith-notifier.init")


def main() -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)

    news_items = scraper.scrape_news(scraper.fetch_html(config.NEWS_URL))
    state_manager.save_json(
        os.path.join(config.STATE_DIR, "news.json"),
        {"known_ids": sorted({i.id for i in news_items}), "queue": [], "consecutive_errors": 0},
    )
    logger.info("news.json: %d件を既読化", len(news_items))

    appli_html = scraper.fetch_html(config.APPLI_URL)

    new_title_items = scraper.scrape_new_titles(appli_html)
    state_manager.save_json(
        os.path.join(config.STATE_DIR, "new_titles.json"),
        {"known_ids": sorted({i.id for i in new_title_items}), "queue": [], "consecutive_errors": 0},
    )
    logger.info("new_titles.json: %d件を既読化", len(new_title_items))

    release_items = scraper.scrape_release_schedule(appli_html)
    games = {}
    skipped = 0
    for item in release_items:
        if item.parsed_date is None:
            skipped += 1
            continue
        # 初回は既存分を全て「通知済み」扱いにする。
        # 後日、年月のみ→月日確定 のように表記が変化した時点で改めて🔁更新通知が飛ぶため、
        # 情報が欠落することはない。
        games[item.id] = {
            "title": item.title,
            "url": item.url,
            "image_url": item.image_url,
            "date_text": item.date_text,
            "precision": item.parsed_date.precision,
            "notified": True,
        }
    state_manager.save_json(
        os.path.join(config.STATE_DIR, "release_schedule.json"),
        {"games": games, "queue": [], "consecutive_errors": 0},
    )
    logger.info("release_schedule.json: %d件を既読化（解析不能%d件はスキップ）", len(games), skipped)


if __name__ == "__main__":
    main()
