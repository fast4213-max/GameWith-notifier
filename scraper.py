"""GameWithの各ページを取得・解析する。

実HTMLを確認した結果、画像・タイトル・IDなどは全てサーバー側で静的HTMLに埋め込まれており
（`data-original` / `gtm-ga4-*` 属性、`<time datetime=...>` 属性）、JS実行は不要。
そのため requests + BeautifulSoup のみで完結する。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from date_utils import ParsedReleaseDate, parse_release_date

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15

logger = logging.getLogger("gamewith-notifier.scraper")

MODULE_TYPE_RELEASE_SCHEDULE = "アプリ発売予定"
MODULE_TYPE_NEW_TITLES = "アプリ新作"


class FetchError(Exception):
    """通信エラー（リトライ枯渇後）。ページ自体は壊れていない一時的な失敗として扱う。"""


class ParseError(Exception):
    """想定していたHTML構造が見つからない。リトライしても直らない、要調査のエラー。"""


@dataclass
class NewsItem:
    id: str  # URLパス全体（例: "gamedb/15584/articles/63543"）。末尾の番号だけでは衝突しうる
    title: str
    url: str
    image_url: Optional[str]
    published_at: str  # ISO8601（<time datetime="...">の値）
    legacy_id: Optional[str] = None  # 旧方式（URL末尾のみ）のID。既存stateとの互換用


@dataclass
class GameListItem:
    id: str
    title: str
    url: str
    image_url: Optional[str]
    date_text: str
    parsed_date: Optional[ParsedReleaseDate]


def fetch_html(url: str, retries: int = 3, backoff_seconds: Iterable[float] = (2, 4, 8)) -> str:
    backoff = list(backoff_seconds)
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
    raise FetchError(f"{url} の取得に{retries}回失敗しました: {last_error}")


def _news_id(url: str) -> str:
    """ニュースIDにはURLのホスト以下のパス全体を使う。

    末尾の数値だけを使うと `/gamedb/1/articles/4338` と `/pc/article/show/4338` が
    同じIDになり、後から来た別記事が「既知」と誤判定されて通知が欠落する。
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    host = parsed.netloc.replace("www.", "")
    # gamewith.jp以外（lp.gamewith.jp等）はホストも含めて一意にする
    return path if host in ("", "gamewith.jp") else f"{host}/{path}"


def _image_url(img_tag) -> Optional[str]:
    if img_tag is None:
        return None
    return img_tag.get("data-original") or img_tag.get("src")


def scrape_news(html: str) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[NewsItem] = []
    for li in soup.select("ul.article-news-list > li.article-news-list_item"):
        a = li.select_one("a.media")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        title_el = a.select_one("._title")
        time_el = a.select_one("time._time")
        if not title_el or not time_el:
            continue
        items.append(
            NewsItem(
                id=_news_id(href),
                title=title_el.get_text(strip=True),
                url=href,
                image_url=_image_url(a.select_one("img")),
                published_at=time_el.get("datetime", ""),
                legacy_id=href.rstrip("/").rsplit("/", 1)[-1],
            )
        )
    if not items:
        raise ParseError("ニュース一覧の要素が1件も取得できませんでした（HTML構造変更の可能性）")
    return items


def _scrape_game_slider(html: str, module_type: str) -> list[GameListItem]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", attrs={"gtm-ga4-module-type": module_type})
    if not container:
        raise ParseError(f"セクション({module_type})が見つかりませんでした（HTML構造変更の可能性）")

    items: list[GameListItem] = []
    for a in container.select("a.gtm-ga4-gdb-link-click-event"):
        game_id = a.get("gtm-ga4-game-id")
        if not game_id:
            continue  # 「もっと見る」等ゲーム項目でないリンクを除外
        title = a.get("gtm-ga4-title") or ""
        href = a.get("href") or ""
        image_url = a.get("gtm-ga4-image-url") or _image_url(a.select_one("img"))
        date_el = a.select_one("._release-date div")
        if not title or not href or not date_el:
            continue
        date_text = date_el.get_text(strip=True)
        url = f"https://gamewith.jp{href}" if href.startswith("/") else href
        parsed_date = parse_release_date(date_text)
        if parsed_date is None:
            # 解析できない表記（「未定」等）は通知対象外。表記が変わるまで毎回ここを通るため
            # 新しい表記パターンが出てきたことに気付けるようログに残す。
            logger.info("日付表記を解析できませんでした（通知対象外）: %s / %r", title, date_text)
        items.append(
            GameListItem(
                id=game_id,
                title=title,
                url=url,
                image_url=image_url,
                date_text=date_text,
                parsed_date=parsed_date,
            )
        )
    if not items:
        raise ParseError(f"セクション({module_type})のゲーム項目が1件も取得できませんでした")
    return items


def scrape_release_schedule(html: str) -> list[GameListItem]:
    return _scrape_game_slider(html, MODULE_TYPE_RELEASE_SCHEDULE)


def scrape_new_titles(html: str) -> list[GameListItem]:
    return _scrape_game_slider(html, MODULE_TYPE_NEW_TITLES)
