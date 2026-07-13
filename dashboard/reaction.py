from __future__ import annotations

import datetime as dt

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

import data as data_mod

_HOLIDAYS = set(USFederalHolidayCalendar().holidays(start="2000-01-01", end="2035-12-31"))

# EIA weekly petroleum status report: data as of Friday, released the following
# Wednesday at 10:30am ET -- shifted a day later when Monday of release week is
# a federal holiday (e.g. Presidents' Day, Labor Day). Not independently
# verifiable from the QH API, so this is the standard published EIA schedule.
RELEASE_HOUR_ET = 10
RELEASE_MINUTE_ET = 30


def release_datetime_utc(data_week_friday: pd.Timestamp) -> pd.Timestamp:
    release_day = data_week_friday + pd.Timedelta(days=5)  # Friday -> Wednesday
    monday = release_day - pd.Timedelta(days=2)
    if monday.normalize() in _HOLIDAYS:
        release_day += pd.Timedelta(days=1)
    naive = release_day.replace(hour=RELEASE_HOUR_ET, minute=RELEASE_MINUTE_ET, second=0, microsecond=0)
    return naive.tz_localize("America/New_York").tz_convert("UTC")


def price_reaction(front_month_code: str, data_week_friday: pd.Timestamp, window_minutes: int = 60) -> dict | None:
    release_utc = release_datetime_utc(data_week_friday)
    start_ts = int((release_utc - pd.Timedelta(minutes=5)).timestamp())
    end_ts = int((release_utc + pd.Timedelta(minutes=window_minutes)).timestamp())

    df = data_mod.fetch_ohlc_v2((front_month_code,), interval="5M", start=start_ts, end=end_ts)
    if df.empty:
        return None

    before = df[df["time"] <= release_utc]
    after = df[df["time"] >= release_utc]
    if before.empty or after.empty:
        return None

    pre_price = before.iloc[-1]["open"]
    post_price = after.iloc[-1]["close"]
    change = post_price - pre_price
    pct_change = (change / pre_price * 100) if pre_price else None

    return {
        "release_time_utc": release_utc,
        "pre_price": pre_price,
        "post_price": post_price,
        "change": change,
        "pct_change": pct_change,
        "window_minutes": window_minutes,
    }
