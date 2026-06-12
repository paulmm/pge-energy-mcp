"""PG&E observed holidays for TOU classification.

Per PG&E tariff sheets, weekday-only peak schedules (E-TOU-D) treat these
holidays as off-peak: New Year's Day, Presidents' Day, Memorial Day,
Independence Day, Labor Day, Veterans Day, Thanksgiving Day, Christmas Day.
A holiday falling on Sunday is observed the following Monday. (Saturday
holidays need no observation — Saturdays are already off-peak.)
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth occurrence of weekday (0=Mon) in month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


@lru_cache(maxsize=None)
def pge_holidays(year: int) -> frozenset:
    holidays = set()
    for d in (date(year, 1, 1), date(year, 7, 4),
              date(year, 11, 11), date(year, 12, 25)):
        holidays.add(d)
        if d.weekday() == 6:  # Sunday -> observed Monday
            holidays.add(d + timedelta(days=1))
    holidays.add(_nth_weekday(year, 2, 0, 3))    # Presidents' Day
    holidays.add(_last_weekday(year, 5, 0))      # Memorial Day
    holidays.add(_nth_weekday(year, 9, 0, 1))    # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving
    return frozenset(holidays)


def is_pge_holiday(d: date) -> bool:
    return d in pge_holidays(d.year)
