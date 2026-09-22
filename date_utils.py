"""GameWithの「9月22日」「26年9月」といった日付表記の解析。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_PAREN = re.compile(r"\(.*?\)")
_DAY_PATTERN = re.compile(r"^(\d{1,2})月(\d{1,2})日$")
_MONTH_ONLY_PATTERN = re.compile(r"^(\d{2})年(\d{1,2})月$")


@dataclass(frozen=True)
class ParsedReleaseDate:
    precision: str  # "day"（月日まで確定） or "month"（年月のみ）
    text: str  # 元の表記（例: "9月22日" / "26年9月"）
    approx_date: date  # 比較・並び替え用（月のみの場合はその月1日とする）


def parse_release_date(text: str, today: date | None = None) -> ParsedReleaseDate | None:
    """「9月22日」→day精度、「26年9月」→month精度。想定外の表記はNoneを返す。"""
    today = today or date.today()
    cleaned = _PAREN.sub("", text).strip()

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
        year = 2000 + int(m.group(1))
        month = int(m.group(2))
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
