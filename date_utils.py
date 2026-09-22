"""GameWithの「9月22日」「26年9月」といった日付表記の解析。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# GitHub Actionsのランナーは常にUTCで動くため、JSTでの「今日」を基準にしないと
# 日本時間 00:00〜09:00 の実行で前日扱いになり、年またぎの推定がずれる。
JST = timezone(timedelta(hours=9))

_PAREN = re.compile(r"[(（].*?[)）]")
_DAY_PATTERN = re.compile(r"^(\d{1,2})月(\d{1,2})日$")
# 「26年9月」「2026年9月」「26年9月下旬」いずれも年月のみの精度として扱う
_MONTH_ONLY_PATTERN = re.compile(r"^(?:(\d{2}|\d{4})年)?(\d{1,2})月(?:上旬|中旬|下旬|初旬|頃|予定)?$")


@dataclass(frozen=True)
class ParsedReleaseDate:
    precision: str  # "day"（月日まで確定） or "month"（年月のみ）
    text: str  # 元の表記（例: "9月22日" / "26年9月"）
    approx_date: date  # 比較・並び替え用（月のみの場合はその月1日とする）


def today_jst() -> date:
    return datetime.now(JST).date()


def parse_release_date(text: str, today: date | None = None) -> ParsedReleaseDate | None:
    """「9月22日」→day精度、「26年9月」→month精度。想定外の表記はNoneを返す。"""
    today = today or today_jst()
    # 全角英数字・全角スペースを半角へ寄せてから記号を落とす（「２６年９月」等の表記ゆれ対策）
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _PAREN.sub("", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)

    m = _DAY_PATTERN.match(cleaned)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            year = _infer_year(today, month, day)
            return ParsedReleaseDate("day", text.strip(), date(year, month, day))
        except ValueError:
            return None

    m = _MONTH_ONLY_PATTERN.match(cleaned)
    if m:
        month = int(m.group(2))
        year = _infer_month_year(today, m.group(1), month)
        try:
            return ParsedReleaseDate("month", text.strip(), date(year, month, 1))
        except ValueError:
            return None

    return None


def _infer_year(today: date, month: int, day: int) -> int:
    """月日のみの表記から年を推定する。180日以上過去になる場合は翌年とみなす（年またぎ対応）。"""
    candidate = date(today.year, month, day)
    if (today - candidate).days > 180:
        return today.year + 1
    return today.year


def _infer_month_year(today: date, year_text: str | None, month: int) -> int:
    """「26年9月」→2026年。年の記載がない「9月」だけの表記は月日と同じ規則で推定する。"""
    if year_text is None:
        return _infer_year(today, month, 1)
    year = int(year_text)
    return year if year >= 1000 else 2000 + year
