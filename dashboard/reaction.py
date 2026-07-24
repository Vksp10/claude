from __future__ import annotations

import pandas as pd

import data as data_mod
import eia_calendar


def price_reaction(instrument_code: str, release_date: pd.Timestamp, window_minutes: int = 60) -> dict | None:
    release_utc = eia_calendar.release_datetime_utc(release_date)
    start_ts = int((release_utc - pd.Timedelta(minutes=5)).timestamp())
    end_ts = int((release_utc + pd.Timedelta(minutes=window_minutes)).timestamp())

    df = data_mod.fetch_ohlc_v2((instrument_code,), interval="5M", start=start_ts, end=end_ts)
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
