from __future__ import annotations

import pandas as pd

import curves
import data as data_mod


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
            rows.append({"label": label, "code": code, "current": current, "compare": compare, "diff": diff})
        structures[name] = pd.DataFrame(rows)

    return {"chain": chain, "structures": structures}


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
