from __future__ import annotations

import pandas as pd
import streamlit as st

import curves
import data as data_mod
import eia_calendar


def _nearest_close(df: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    if df.empty:
        return None
    sub = df[df["date_naive"] <= as_of]
    if sub.empty:
        return None
    return float(sub.iloc[-1]["close"])


def _with_naive_dates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["date_naive"] = df["time"].dt.tz_localize(None)
    return df


def _fill_from_adjacent(
    target: pd.DataFrame,
    base: pd.DataFrame,
    base_name: str,
) -> pd.DataFrame:
    """Fill missing structure values from adjacent lower-order structures.

    1MF[i]  = 1MS[i] - 1MS[i+1]
    1MDF[i] = 1MF[i] - 1MF[i+1]

    API values remain authoritative; the calculation is only a fallback for
    individual dates/tenors where QH returns no combo history.
    """
    target = target.copy()
    for field in ("current", "compare"):
        source_col = f"{field}_source"
        for i in range(len(target)):
            if pd.notna(target.at[i, field]) or i + 1 >= len(base):
                continue
            front = base.at[i, field]
            back = base.at[i + 1, field]
            if pd.notna(front) and pd.notna(back):
                target.at[i, field] = float(front) - float(back)
                target.at[i, source_col] = f"Calculated from adjacent {base_name}"

    target["diff"] = target["current"] - target["compare"]
    return target


def curve_strip_snapshot(selected_week: pd.Timestamp, compare_week: pd.Timestamp, n_months: int) -> dict:
    """Full forward-strip 1MS/1MF/1MDF snapshot: current (selected week) vs a
    comparison week, across `n_months` consecutive contracts starting at the
    nearest-unexpired contract as of the selected week."""
    chain = curves.front_month_chain(selected_week.date(), n_months)
    window_start = min(selected_week, compare_week) - pd.Timedelta(weeks=2)
    window_end = max(selected_week, compare_week) + pd.Timedelta(weeks=2)

    structures: dict[str, pd.DataFrame] = {}
    for name, legs in (("1MS", 2), ("1MF", 3), ("1MDF", 4)):
        strip = curves.combo_strip_codes(chain, legs)
        codes = tuple(code for code, _ in strip)
        raw = data_mod.fetch_ohlc_v2(
            codes, interval="1D", start=int(window_start.timestamp()), end=int(window_end.timestamp())
        )
        df = _with_naive_dates(raw)
        rows = []
        for code, label in strip:
            sub = df[df["product"] == code] if not df.empty else df
            current = _nearest_close(sub, selected_week)
            compare = _nearest_close(sub, compare_week)
            diff = current - compare if current is not None and compare is not None else None
            rows.append(
                {
                    "label": label,
                    "code": code,
                    "current": current,
                    "compare": compare,
                    "diff": diff,
                    "current_source": "QH API" if current is not None else None,
                    "compare_source": "QH API" if compare is not None else None,
                }
            )
        structures[name] = pd.DataFrame(rows)

    structures["1MF"] = _fill_from_adjacent(structures["1MF"], structures["1MS"], "1MS")
    structures["1MDF"] = _fill_from_adjacent(structures["1MDF"], structures["1MF"], "1MF")

    return {"chain": chain, "structures": structures}


@st.cache_data(ttl=3600, show_spinner=False)
def historical_1ms_reactions(
    all_weeks: list[pd.Timestamp],
    lookback_weeks: int = 26,
    window_minutes: int = 60,
) -> pd.DataFrame:
    """One-hour RB 1MS reaction for historical EIA releases.

    Each API request is restricted to the 65-minute event window. This is much
    faster than downloading month-wide five-minute histories and the completed
    result is cached for subsequent dashboard interactions.
    """
    if not all_weeks:
        return pd.DataFrame()

    selected_weeks = all_weeks[-lookback_weeks:]
    mappings = []
    for week in selected_weeks:
        chain = curves.front_month_chain(week.date(), 2)
        mappings.append(
            {
                "week": week,
                "spread_code": curves.combo_code(chain, 2),
                "release_utc": eia_calendar.release_datetime_utc(week),
            }
        )

    rows = []
    for release in mappings:
        spread_code = release["spread_code"]
        release_utc = release["release_utc"]
        row = {
            "week": release["week"],
            "spread_code": spread_code,
            "pre_price": None,
            "post_price": None,
            "move_1h": None,
            "pct_move_1h": None,
        }
        try:
            bars = data_mod.fetch_ohlc_v2(
                (spread_code,),
                interval="5M",
                start=int((release_utc - pd.Timedelta(minutes=5)).timestamp()),
                end=int((release_utc + pd.Timedelta(minutes=window_minutes)).timestamp()),
            )
        except Exception:
            bars = pd.DataFrame()

        if bars.empty:
            rows.append(row)
            continue

        bars = bars[bars["product"] == spread_code].sort_values("time")
        before = bars[bars["time"] <= release_utc]
        after = bars[bars["time"] >= release_utc]
        if before.empty or after.empty:
            rows.append(row)
            continue

        pre_price = float(before.iloc[-1]["open"])
        post_price = float(after.iloc[-1]["close"])
        move = post_price - pre_price
        row.update(
            {
                "pre_price": pre_price,
                "post_price": post_price,
                "move_1h": move,
                "pct_move_1h": move / pre_price * 100 if pre_price else None,
            }
        )
        rows.append(row)

    return pd.DataFrame(rows).sort_values("week").reset_index(drop=True)


def crack_series(center_week: pd.Timestamp, weeks_before: int, weeks_after: int, all_weeks: list[pd.Timestamp]) -> pd.DataFrame:
    """Front-month RB-CL crack spread across a window of weeks, re-resolving the
    front-month contract for each week so contract rolls don't create fake jumps."""
    try:
        idx = all_weeks.index(center_week)
        lo, hi = max(0, idx - weeks_before), min(len(all_weeks) - 1, idx + weeks_after)
        window_weeks = all_weeks[lo : hi + 1]
    except ValueError:
        window_weeks = [center_week + pd.Timedelta(weeks=o) for o in range(-weeks_before, weeks_after + 1)]

    codes_by_week = {wk: curves.curve_codes(wk.date())["crack_front"] for wk in window_weeks}
    distinct_codes = tuple(sorted(set(codes_by_week.values())))

    span_start = min(window_weeks) - pd.Timedelta(weeks=1)
    span_end = max(window_weeks) + pd.Timedelta(days=3)
    raw = data_mod.fetch_ohlc_v2(
        distinct_codes, interval="1D", start=int(span_start.timestamp()), end=int(span_end.timestamp())
    )
    df = _with_naive_dates(raw)

    rows = []
    for wk in window_weeks:
        code = codes_by_week[wk]
        price = _nearest_close(df[df["product"] == code] if not df.empty else df, wk)
        rows.append({"week": wk, "front_month": code, "crack": price})
    return pd.DataFrame(rows)
