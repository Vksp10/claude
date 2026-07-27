from __future__ import annotations

import datetime as dt

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

MONTH_CODES = "FGHJKMNQUVXZ"  # NYMEX month codes, Jan..Dec

_HOLIDAYS = USFederalHolidayCalendar().holidays(start="2000-01-01", end="2035-12-31")
_BDAY = pd.offsets.CustomBusinessDay(holidays=_HOLIDAYS)


def month_code(month: int) -> str:
    return MONTH_CODES[month - 1]


def month_year_code(year: int, month: int) -> str:
    """e.g. (2026, 1) -> 'F26'"""
    return f"{month_code(month)}{year % 100:02d}"


def contract_code(year: int, month: int, product: str = "RB") -> str:
    """Standalone futures contract code, e.g. (2026, 1, "HO") -> "HOF26"."""
    return f"{product}{month_year_code(year, month)}"


def _last_business_day(year: int, month: int) -> pd.Timestamp:
    last = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    if last.weekday() >= 5 or last in _HOLIDAYS:
        last = last - _BDAY
    return last


def expiry_date(year: int, month: int) -> pd.Timestamp:
    """RB and HO futures expire on the last business day of the month before
    the contract month (standard NYMEX convention)."""
    prev_month_end = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
    return _last_business_day(prev_month_end.year, prev_month_end.month)


def _add_month(year: int, month: int, n: int = 1) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + n
    return total // 12, total % 12 + 1


def front_month_chain(as_of: dt.date, n: int = 4) -> list[tuple[int, int]]:
    """Nearest unexpired contract as of `as_of`, plus the next (n-1) consecutive months."""
    as_of_ts = pd.Timestamp(as_of)
    year, month = as_of_ts.year, as_of_ts.month
    while expiry_date(year, month) < as_of_ts:
        year, month = _add_month(year, month)
    chain = []
    y, m = year, month
    for _ in range(n):
        chain.append((y, m))
        y, m = _add_month(y, m)
    return chain


def combo_code(chain: list[tuple[int, int]], legs: int, product: str = "RB") -> str:
    """Build a QH combo instrument code (spread/fly/double-fly) from the first `legs`
    entries of a front-month chain, e.g. legs=2 -> 'RBF26-G26'."""
    codes = [month_year_code(y, m) for y, m in chain[:legs]]
    return product + "-".join(codes)


def combo_strip_codes(
    chain: list[tuple[int, int]], legs: int, product: str = "RB"
) -> list[tuple[str, str]]:
    """Rolling combo codes across a whole contract chain, e.g. legs=2 on
    [F26,G26,H26,J26] -> [('RBF26-G26','F26'), ('RBG26-H26','G26'), ('RBH26-J26','H26')].
    The second element of each tuple is the leading-leg label, used as the x-axis tick."""
    codes = [month_year_code(y, m) for y, m in chain]
    out = []
    for i in range(len(codes) - legs + 1):
        out.append((product + "-".join(codes[i : i + legs]), codes[i]))
    return out


def curve_codes(as_of: dt.date, n_months: int = 4, product: str = "RB") -> dict:
    """All instrument codes needed to render the curve-structure panel for one week."""
    chain = front_month_chain(as_of, n_months)
    legs = [contract_code(y, m, product) for y, m in chain]
    spread = combo_code(chain, 2, product) if len(chain) >= 2 else None
    fly = combo_code(chain, 3, product) if len(chain) >= 3 else None
    double_fly = combo_code(chain, 4, product) if len(chain) >= 4 else None
    crack_front = f"{product}CL{month_year_code(*chain[0])}"
    return {
        "chain": chain,
        "legs": legs,
        "spread": spread,
        "fly": fly,
        "double_fly": double_fly,
        "front_month": legs[0],
        "crack_front": crack_front,
    }
