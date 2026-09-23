"""回帰テスト（標準ライブラリのunittestのみ。`python tests.py` で実行）。

外部通信は行わない。スクレイピングはこのファイル内の最小HTMLに対して検証する。
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

import config
import differ
import main as main_module
import notifier
import scraper
from date_utils import parse_release_date
from scraper import GameListItem

TODAY = date(2026, 9, 22)


def game(game_id: str, date_text: str) -> GameListItem:
    return GameListItem(
        id=game_id,
        title=f"ゲーム{game_id}",
        url=f"https://gamewith.jp/gamedb/{game_id}",
        image_url=None,
        date_text=date_text,
        parsed_date=parse_release_date(date_text, TODAY),
    )


class TestParseReleaseDate(unittest.TestCase):
    def test_day_and_month_precision(self):
        self.assertEqual(parse_release_date("9月22日", TODAY).precision, "day")
        self.assertEqual(parse_release_date("26年9月", TODAY).precision, "month")

    def test_four_digit_year(self):
        parsed = parse_release_date("2026年9月", TODAY)
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.precision, parsed.approx_date), ("month", date(2026, 9, 1)))

    def test_full_width_parenthesis_and_digits(self):
        self.assertEqual(parse_release_date("9月22日（月）", TODAY).approx_date, date(2026, 9, 22))
        self.assertEqual(parse_release_date("２６年９月", TODAY).approx_date, date(2026, 9, 1))

    def test_jun_suffix(self):
        self.assertEqual(parse_release_date("26年9月下旬", TODAY).precision, "month")

    def test_year_rollover(self):
        # 9月時点の「1月15日」は翌年扱い
        self.assertEqual(parse_release_date("1月15日", TODAY).approx_date, date(2027, 1, 15))

    def test_leap_day_in_non_leap_year(self):
        self.assertEqual(parse_release_date("2月29日", date(2027, 9, 22)).approx_date, date(2028, 2, 29))

    def test_unparseable(self):
        self.assertIsNone(parse_release_date("未定", TODAY))
        self.assertIsNone(parse_release_date("13月40日", TODAY))


class TestNewsId(unittest.TestCase):
    HTML = """<ul class="article-news-list">
      <li class="article-news-list_item"><a class="media" href="https://gamewith.jp/gamedb/1/articles/4338">
        <span class="_title">gamedb記事</span><time class="_time" datetime="2026-09-22T10:00+09:00"></time></a></li>
      <li class="article-news-list_item"><a class="media" href="https://gamewith.jp/pc/article/show/4338">
        <span class="_title">pc記事</span><time class="_time" datetime="2026-09-22T09:00+09:00"></time></a></li>
    </ul>"""

    def test_ids_do_not_collide(self):
        items = scraper.scrape_news(self.HTML)
        self.assertEqual([i.id for i in items], ["gamedb/1/articles/4338", "pc/article/show/4338"])

    def test_distinct_articles_are_both_new(self):
        items = scraper.scrape_news(self.HTML)
        new_items, _ = differ.diff_simple_list(items, set())
        self.assertEqual(len(new_items), 2)

    def test_legacy_ids_are_still_known(self):
        """ID方式の変更で、既読済みの記事が一斉に再通知されないこと。"""
        html = self.HTML.replace("pc/article/show/4338", "pc/article/show/9999")
        items = scraper.scrape_news(html)
        # 旧方式（URL末尾のみ）で記録された既読IDでも既知と判定される
        new_items, known = differ.diff_simple_list(items, {"4338", "9999"})
        self.assertEqual(new_items, [])
        self.assertEqual(known[:2], ["gamedb/1/articles/4338", "pc/article/show/9999"])

    def test_state_is_migrated_to_new_ids(self):
        """1度実行すれば、掲載中の記事は新方式のIDで記録し直される。"""
        items = scraper.scrape_news(self.HTML)
        _, known = differ.diff_simple_list(items, {"4338"}, ["4338"])
        self.assertEqual(known[:2], ["gamedb/1/articles/4338", "pc/article/show/4338"])

    def test_parse_error_when_empty(self):
        with self.assertRaises(scraper.ParseError):
            scraper.scrape_news("<html></html>")

    def test_legacy_ids_are_dropped_after_migration(self):
        """移行後に旧IDが残り、末尾番号が同じ別記事を取りこぼさないこと。"""
        items = scraper.scrape_news(self.HTML.replace("pc/article/show/4338", "pc/article/show/9999"))
        _, known = differ.diff_simple_list(items, {"4338", "5555"}, ["4338", "5555"])
        known = differ.drop_legacy_news_ids(known, {i.id for i in items})
        self.assertEqual(known, ["gamedb/1/articles/4338", "pc/article/show/9999"])

        later = scraper.scrape_news(self.HTML.replace("gamedb/1/articles/4338", "pc/article/show/5555"))
        new_items, _ = differ.diff_simple_list(later, set(known), known)
        self.assertEqual([i.id for i in new_items], ["pc/article/show/5555", "pc/article/show/4338"])

    def test_single_segment_id_on_page_is_kept(self):
        html = self.HTML.replace("https://gamewith.jp/gamedb/1/articles/4338", "https://gamewith.jp/special")
        items = scraper.scrape_news(html)
        _, known = differ.diff_simple_list(items, set())
        self.assertIn("special", differ.drop_legacy_news_ids(known, {i.id for i in items}))


class TestDiffSimpleList(unittest.TestCase):
    def test_known_ids_are_capped_and_recency_ordered(self):
        old = [f"old{i}" for i in range(differ.KNOWN_IDS_LIMIT + 50)]
        items = [game("new1", "9月22日")]
        _, known = differ.diff_simple_list(items, set(old), old)
        self.assertEqual(len(known), differ.KNOWN_IDS_LIMIT)
        self.assertEqual(known[0], "new1")  # 直近に見たものが先頭
        self.assertIn("old0", known)  # 掲載中に近い分は残る

    def test_duplicate_items_notify_once(self):
        items = [game("1", "9月22日"), game("2", "9月22日"), game("1", "9月22日")]
        new_items, known = differ.diff_simple_list(items, set())
        self.assertEqual([i.id for i in new_items], ["1", "2"])
        self.assertEqual(known, ["1", "2"])


class TestDiffReleaseSchedule(unittest.TestCase):
    def test_month_only_is_recorded_but_not_notified(self):
        notify, state = differ.diff_release_schedule([game("1", "26年12月")], {})
        self.assertEqual(notify, [])
        self.assertFalse(state["1"]["notified"])

    def test_day_precision_notifies(self):
        notify, state = differ.diff_release_schedule([game("1", "9月22日")], {})
        self.assertEqual([n["kind"] for n in notify], ["new"])
        self.assertTrue(state["1"]["notified"])

    def test_pending_becomes_day(self):
        _, state = differ.diff_release_schedule([game("1", "26年12月")], {})
        notify, state = differ.diff_release_schedule([game("1", "12月3日")], state)
        self.assertEqual([n["kind"] for n in notify], ["new"])

    def test_postpone_to_another_day(self):
        _, state = differ.diff_release_schedule([game("1", "9月22日")], {})
        notify, _ = differ.diff_release_schedule([game("1", "10月5日")], state)
        self.assertEqual([n["kind"] for n in notify], ["updated"])

    def test_postpone_to_month_precision_notifies(self):
        """確定日→年月のみへの延期も通知し、stateも追随すること。"""
        _, state = differ.diff_release_schedule([game("1", "9月22日")], {})
        notify, state = differ.diff_release_schedule([game("1", "26年12月")], state)
        self.assertEqual([n["kind"] for n in notify], ["updated"])
        self.assertEqual(state["1"]["date_text"], "26年12月")

    def test_no_change_no_notification(self):
        _, state = differ.diff_release_schedule([game("1", "9月22日")], {})
        notify, _ = differ.diff_release_schedule([game("1", "9月22日")], state)
        self.assertEqual(notify, [])

    def test_unparseable_date_is_skipped(self):
        notify, state = differ.diff_release_schedule([game("1", "未定")], {})
        self.assertEqual((notify, state), ([], {}))

    def test_unparseable_date_keeps_existing_record_alive(self):
        """掲載中の項目が「未定」表記になっても、保持期限切れで消されないこと。"""
        record = {"title": "ゲーム1", "date_text": "9月22日", "precision": "day", "notified": True,
                  "last_seen": "2020-01-01"}
        notify, state = differ.diff_release_schedule([game("1", "未定")], {"1": record})
        self.assertEqual(notify, [])
        self.assertEqual(state["1"]["date_text"], "9月22日")
        self.assertNotEqual(state["1"]["last_seen"], "2020-01-01")

    def test_notation_only_change_does_not_notify(self):
        _, state = differ.diff_release_schedule([game("1", "9月22日")], {})
        notify, state = differ.diff_release_schedule([game("1", "9月22日（火）")], state)
        self.assertEqual(notify, [])
        self.assertEqual(state["1"]["date_text"], "9月22日（火）")

    def test_stale_entries_are_pruned(self):
        stale = {"old": {"title": "昔のゲーム", "date_text": "1月1日", "notified": True,
                         "last_seen": "2020-01-01"}}
        _, state = differ.diff_release_schedule([game("1", "9月22日")], stale)
        self.assertNotIn("old", state)
        self.assertIn("1", state)

    def test_records_without_last_seen_are_kept(self):
        legacy = {"old": {"title": "既存", "date_text": "1月1日", "notified": True}}
        _, state = differ.diff_release_schedule([], legacy)
        self.assertIn("old", state)


class TestDrainAndNotify(unittest.TestCase):
    def test_permanent_error_does_not_block_the_queue(self):
        """400で弾かれる1件がqueue先頭に居座り、後続を永久に止めないこと。"""
        bad, good = {"title": "bad"}, {"title": "good"}

        def send(_url, embed):
            if embed["title"] == "bad":
                raise notifier.PermanentNotifyError("HTTP 400")

        with mock.patch.object(notifier, "send_embed", side_effect=send):
            carry = main_module._drain_and_notify([bad], [good], "https://example.invalid", "テスト")
        self.assertEqual(carry, [])

    def test_transient_error_requeues_the_failed_item(self):
        first, second = {"title": "1"}, {"title": "2"}
        with mock.patch.object(notifier, "send_embed", side_effect=RuntimeError("timeout")):
            carry = main_module._drain_and_notify([], [first, second], "https://example.invalid", "テスト")
        self.assertEqual(carry, [first, second])  # 送信できなかった分は失われない

    def test_queue_is_capped(self):
        embeds = [{"title": str(i)} for i in range(config.MAX_QUEUE_SIZE + 60)]
        with mock.patch.object(notifier, "send_embed", side_effect=RuntimeError("down")):
            carry = main_module._drain_and_notify([], embeds, "https://example.invalid", "テスト")
        self.assertEqual(len(carry), config.MAX_QUEUE_SIZE)
        self.assertEqual(carry[-1]["title"], str(len(embeds) - 1))  # 新しい方を残す


class TestNotifier(unittest.TestCase):
    def test_long_title_is_truncated(self):
        embed = notifier._sanitize({"title": "あ" * 400, "fields": [{"name": "n", "value": "い" * 2000}]})
        self.assertEqual(len(embed["title"]), notifier.TITLE_MAX)
        self.assertEqual(len(embed["fields"][0]["value"]), notifier.FIELD_VALUE_MAX)

    def test_empty_webhook_raises(self):
        with self.assertRaises(RuntimeError):
            notifier.send_embed("", {"title": "x"})

    def test_client_error_is_permanent(self):
        resp = mock.Mock(status_code=400, text="bad request")
        with mock.patch.object(notifier.requests, "post", return_value=resp):
            with self.assertRaises(notifier.PermanentNotifyError):
                notifier.send_embed("https://example.invalid", {"title": "x"})


class TestParseErrorSuppression(unittest.TestCase):
    def test_same_error_notifies_only_once(self):
        state: dict = {}
        exc = scraper.ParseError("構造が変わりました")
        with mock.patch.object(main_module, "_notify_error_to_news") as notify:
            main_module._handle_parse_error(state, "テスト", exc)
            main_module._handle_parse_error(state, "テスト", exc)
        self.assertEqual(notify.call_count, 1)

    def test_different_error_notifies_again(self):
        state: dict = {}
        with mock.patch.object(main_module, "_notify_error_to_news") as notify:
            main_module._handle_parse_error(state, "テスト", scraper.ParseError("A"))
            main_module._handle_parse_error(state, "テスト", scraper.ParseError("B"))
        self.assertEqual(notify.call_count, 2)


class TestAppliPage(unittest.TestCase):
    def test_page_is_fetched_only_once(self):
        with mock.patch.object(scraper, "fetch_html", return_value="<html></html>") as fetch:
            page = main_module._AppliPage()
            page.html()
            page.html()
        self.assertEqual(fetch.call_count, 1)

    def test_fetch_error_is_cached_and_reraised(self):
        with mock.patch.object(scraper, "fetch_html", side_effect=scraper.FetchError("失敗")) as fetch:
            page = main_module._AppliPage()
            for _ in range(2):
                with self.assertRaises(scraper.FetchError):
                    page.html()
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
