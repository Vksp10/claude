from __future__ import annotations

import pandas as pd
import streamlit as st

import curves
import data as data_mod
import eia_calendar

PRODUCT_1MS_TICK_SIZE = 0.0001
PRODUCT_1MS_TICK_VALUE = 4.2


def _resolve_1ms_code(
    week: pd.Timestamp,
    spread_offset: int = 0,
    specific_spread: str | None = None,
    product: str = "RB",
) -> str:
    """Resolve a fixed or rolling adjacent-month product spread.

    spread_offset=0 is front 1MS (M1-M2), 1 is second 1MS (M2-M3), and 2 is
    third 1MS (M3-M4).
    """
    if specific_spread:
        return specific_spread
    chain = curves.front_month_chain(week.date(), int(spread_offset) + 2)
    return curves.combo_code(chain[int(spread_offset) :], 2, product)


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


def curve_strip_snapshot(
    selected_week: pd.Timestamp,
    compare_week: pd.Timestamp,
    n_months: int,
    product: str = "RB",
) -> dict:
    """Full forward-strip 1MS/1MF/1MDF snapshot: current (selected week) vs a
    comparison week, across `n_months` consecutive contracts starting at the
    nearest-unexpired contract as of the selected week."""
    chain = curves.front_month_chain(selected_week.date(), n_months)
    window_start = min(selected_week, compare_week) - pd.Timedelta(weeks=2)
    window_end = max(selected_week, compare_week) + pd.Timedelta(weeks=2)

    structures: dict[str, pd.DataFrame] = {}
    for name, legs in (("1MS", 2), ("1MF", 3), ("1MDF", 4)):
        strip = curves.combo_strip_codes(chain, legs, product)
        codes = tuple(code for code, _ in strip)
        try:
            raw = data_mod.fetch_ohlc_v2(
                codes,
                interval="1D",
                start=int(window_start.timestamp()),
                end=int(window_end.timestamp()),
            )
        except Exception:
            raw = pd.DataFrame()
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


def historical_1ms_reactions(
    all_weeks: list[pd.Timestamp],
    lookback_weeks: int = 26,
    window_minutes: int = 60,
    spread_offset: int = 0,
    specific_spread: str | None = None,
    product: str = "RB",
) -> pd.DataFrame:
    """One-hour product 1MS reaction for historical EIA releases.

    Each API request is restricted to the 65-minute event window. This is much
    faster than downloading month-wide five-minute histories and the completed
    result is cached for subsequent dashboard interactions.
    """
    if not all_weeks:
        return pd.DataFrame()

    selected_weeks = all_weeks[-lookback_weeks:]
    mappings = []
    for week in selected_weeks:
        mappings.append(
            {
                "week": week,
                "spread_code": _resolve_1ms_code(
                    week,
                    spread_offset=spread_offset,
                    specific_spread=specific_spread,
                    product=product,
                ),
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


def backtest_inventory_signal_1ms(
    signal_rows: tuple[tuple[str, float], ...],
    lookback_months: int,
    holding_minutes: int,
    stop_dollars: float,
    target_mode: str,
    target_value: float,
    spread_offset: int = 0,
    specific_spread: str | None = None,
    product: str = "RB",
) -> pd.DataFrame:
    """Backtest draw-long/build-short product 1MS trades around EIA releases.

    The trade enters at the open of the first five-minute bar timestamped at or
    after the EIA release. Stops and targets are expressed as P&L dollars for
    one product 1MS spread, using a 0.0001 tick worth $4.20. If both levels trade in
    the same bar, the stop is assumed to fill first (conservative sequencing).
    """
    if not signal_rows:
        return pd.DataFrame()

    signals = pd.DataFrame(signal_rows, columns=["week", "inventory_change"])
    signals["week"] = pd.to_datetime(signals["week"])
    signals["inventory_change"] = pd.to_numeric(
        signals["inventory_change"], errors="coerce"
    )
    signals = signals.dropna().sort_values("week")
    if signals.empty:
        return pd.DataFrame()

    cutoff = signals["week"].max() - pd.DateOffset(months=int(lookback_months))
    signals = signals[signals["week"] >= cutoff]
    stop_offset = (
        float(stop_dollars) / PRODUCT_1MS_TICK_VALUE * PRODUCT_1MS_TICK_SIZE
    )
    target_dollars = (
        float(stop_dollars) * float(target_value)
        if target_mode == "Stop multiple"
        else float(target_value)
    )
    target_offset = (
        target_dollars / PRODUCT_1MS_TICK_VALUE * PRODUCT_1MS_TICK_SIZE
    )

    results = []
    for signal in signals.itertuples(index=False):
        week = pd.Timestamp(signal.week)
        inventory_change = float(signal.inventory_change)
        if inventory_change == 0:
            continue

        direction = 1 if inventory_change < 0 else -1
        side = "Long" if direction == 1 else "Short"
        signal_name = "Draw" if direction == 1 else "Build"
        spread_code = _resolve_1ms_code(
            week,
            spread_offset=spread_offset,
            specific_spread=specific_spread,
            product=product,
        )
        release_utc = eia_calendar.release_datetime_utc(week)
        holding_end = release_utc + pd.Timedelta(minutes=int(holding_minutes))

        try:
            event_bars = data_mod.fetch_ohlc_v2(
                (spread_code,),
                interval="5M",
                # Match the reaction-panel request window so the shared
                # fetch_ohlc_v2 cache prevents duplicate API calls.
                start=int((release_utc - pd.Timedelta(minutes=5)).timestamp()),
                end=int(holding_end.timestamp()),
            )
        except Exception:
            event_bars = pd.DataFrame()

        if event_bars.empty:
            continue
        event_bars = event_bars[
            (event_bars["product"] == spread_code)
            & (event_bars["time"] >= release_utc)
            & (event_bars["time"] <= holding_end)
        ].sort_values("time")
        if event_bars.empty:
            continue

        entry_bar = event_bars.iloc[0]
        entry_price = float(entry_bar["open"])
        stop_price = entry_price - direction * stop_offset
        target_price = entry_price + direction * target_offset
        exit_price = float(event_bars.iloc[-1]["close"])
        exit_time = event_bars.iloc[-1]["time"]
        exit_reason = "Holding-period exit"

        for bar in event_bars.itertuples(index=False):
            bar_high = float(bar.high)
            bar_low = float(bar.low)
            if direction == 1:
                stop_hit = bar_low <= stop_price
                target_hit = bar_high >= target_price
            else:
                stop_hit = bar_high >= stop_price
                target_hit = bar_low <= target_price

            if stop_hit:
                exit_price = stop_price
                exit_time = bar.time
                exit_reason = "Stop"
                break
            if target_hit:
                exit_price = target_price
                exit_time = bar.time
                exit_reason = "Target"
                break

        signed_price_move = (exit_price - entry_price) * direction
        pnl_dollars = (
            signed_price_move / PRODUCT_1MS_TICK_SIZE * PRODUCT_1MS_TICK_VALUE
        )
        results.append(
            {
                "week": week,
                "signal": signal_name,
                "side": side,
                "inventory_change": inventory_change,
                "spread_code": spread_code,
                "entry_time": entry_bar["time"],
                "exit_time": exit_time,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl_dollars": pnl_dollars,
            }
        )

    if not results:
        return pd.DataFrame()
    result = pd.DataFrame(results).sort_values("week").reset_index(drop=True)
    result["cumulative_pnl"] = result["pnl_dollars"].cumsum()
    return result


def crack_series(
    center_week: pd.Timestamp,
    weeks_before: int,
    weeks_after: int,
    all_weeks: list[pd.Timestamp],
    product: str = "RB",
) -> pd.DataFrame:
    """Front-month product-CL crack spread across a window of weeks, re-resolving the
    front-month contract for each week so contract rolls don't create fake jumps."""
    try:
        idx = all_weeks.index(center_week)
        lo, hi = max(0, idx - weeks_before), min(len(all_weeks) - 1, idx + weeks_after)
        window_weeks = all_weeks[lo : hi + 1]
    except ValueError:
        window_weeks = [center_week + pd.Timedelta(weeks=o) for o in range(-weeks_before, weeks_after + 1)]

    codes_by_week = {
        wk: curves.curve_codes(wk.date(), product=product)["crack_front"]
        for wk in window_weeks
    }
    distinct_codes = tuple(sorted(set(codes_by_week.values())))

    span_start = min(window_weeks) - pd.Timedelta(weeks=1)
    span_end = max(window_weeks) + pd.Timedelta(days=3)
    try:
        raw = data_mod.fetch_ohlc_v2(
            distinct_codes,
            interval="1D",
            start=int(span_start.timestamp()),
            end=int(span_end.timestamp()),
        )
    except Exception:
        raw = pd.DataFrame()
    df = _with_naive_dates(raw)

    rows = []
    for wk in window_weeks:
        code = codes_by_week[wk]
        price = _nearest_close(df[df["product"] == code] if not df.empty else df, wk)
        rows.append({"week": wk, "front_month": code, "crack": price})
    return pd.DataFrame(rows)
