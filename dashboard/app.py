import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import analytics
import curves
import data as data_mod
import reaction as reaction_mod

REACTION_WINDOW_MINUTES = 60
DEFAULT_HISTORY_YEARS = 5
MAX_HISTORY_YEARS = 15

st.set_page_config(page_title="US Gasoline Inventories & RB Structure", layout="wide")
st.title("US Gasoline Inventories → RB Curve Structure & Pricing")

today = dt.date.today()

# Use complete calendar years rather than a rolling day window. For example,
# five years in 2026 loads data from 2022-01-01 onward.
history_years = st.selectbox(
    "Number of years",
    options=list(range(1, MAX_HISTORY_YEARS + 1)),
    index=DEFAULT_HISTORY_YEARS - 1,
    help="Controls how many calendar years are loaded. The default is 5 years.",
)
history_start_year = today.year - history_years + 1
full_start = dt.date(history_start_year, 1, 1).isoformat()

with st.spinner("Loading gasoline stocks data..."):
    stocks_df = data_mod.fetch_fundamental_series(data_mod.GASOLINE_STOCKS_QHCODE, start_date=full_start)

if stocks_df.empty:
    st.error("No gasoline stocks data returned from the QH API.")
    st.stop()

stocks_df["wow_change"] = stocks_df["actual"].diff()
stocks_df["wow_pct"] = stocks_df["actual"].pct_change() * 100
all_weeks = list(stocks_df["date"])
view_df = stocks_df

st.caption(
    f"Showing {len(view_df)} weekly reports from {view_df['date'].min().date()} to {view_df['date'].max().date()} "
    f"(loaded {history_years} calendar year{'s' if history_years != 1 else ''}, starting {full_start})."
)

# ---------------------------------------------------------------- Full-width inventory chart
# Seasonal palette: muted history, most recent year bold/highlighted.
_SEASONAL_PALETTE = ["#d62728", "#ff7f0e", "#2ca02c", "#9467bd", "#1f77b4", "#8c564b", "#e377c2", "#7f7f7f"]

st.subheader("US Gasoline Stocks - Weekly (EIA)")
view_mode = st.radio(
    "View", ["Absolute Inventory", "Week-on-Week Change", "% Week-on-Week Change"], horizontal=True
)

if view_mode == "Absolute Inventory":
    metric_col, y_title = "actual", "K BBL"
elif view_mode == "Week-on-Week Change":
    metric_col, y_title = "wow_change", "Change (K BBL)"
else:
    metric_col, y_title = "wow_pct", "% Change"

plot_df = view_df.copy()
plot_df["plot_year"] = plot_df["date"].dt.year
plot_df["plot_date"] = plot_df["date"].apply(lambda d: d.replace(year=2000))
years_sorted = sorted(plot_df["plot_year"].unique())

fig = go.Figure()
for i, yr in enumerate(years_sorted):
    yr_df = plot_df[plot_df["plot_year"] == yr].sort_values("plot_date")
    is_latest = yr == years_sorted[-1]
    customdata = yr_df[["date", "actual", "wow_change", "wow_pct"]].astype(str).to_numpy()
    fig.add_trace(
        go.Scatter(
            x=yr_df["plot_date"],
            y=yr_df[metric_col],
            mode="lines+markers",
            name=str(yr),
            customdata=customdata,
            line=dict(
                color=_SEASONAL_PALETTE[i % len(_SEASONAL_PALETTE)],
                width=3 if is_latest else 1.5,
            ),
            marker=dict(size=5 if is_latest else 3),
            hovertemplate=(
                "Week: %{customdata[0]}<br>"
                "Value: %{customdata[1]} K BBL<br>"
                "WoW: %{customdata[2]}<br>"
                "%% Change: %{customdata[3]}%<extra>" + str(yr) + "</extra>"
            ),
        )
    )
fig.update_layout(
    height=500,
    yaxis_title=y_title,
    xaxis=dict(tickformat="%m-%d"),
    margin=dict(t=20, b=20),
    legend_title_text="Year (click to toggle)",
)

click_event = st.plotly_chart(
    fig, on_select="rerun", selection_mode="points", key="stocks_chart", use_container_width=True
)

clicked_date = None
try:
    points = click_event["selection"]["points"]
    if points:
        clicked_date = pd.Timestamp(points[0]["customdata"][0])
except Exception:
    clicked_date = None

week_options = list(view_df["date"])
if "selected_week_slider" not in st.session_state or st.session_state["selected_week_slider"] not in week_options:
    st.session_state["selected_week_slider"] = week_options[-1]
if clicked_date in week_options:
    st.session_state["selected_week_slider"] = clicked_date

selected_week = st.select_slider(
    "Selected week (click a point above, or use this slider)",
    options=week_options,
    format_func=lambda d: d.strftime("%Y-%m-%d"),
    key="selected_week_slider",
)
st.markdown(f"**Selected week: {selected_week.date()}**")

# ---------------------------------------------------------------- Full-width curve row
st.divider()
st.subheader("RB Curve Structure")
ctrl_cols = st.columns(2)
contract_range = ctrl_cols[0].number_input(
    "Contract range (months forward)", min_value=4, max_value=30, value=19, step=1, key="contract_range"
)
try:
    default_compare_idx = max(0, all_weeks.index(selected_week) - 1)
except ValueError:
    default_compare_idx = max(0, len(all_weeks) - 2)
compare_week = ctrl_cols[1].selectbox(
    "Change from (compare date)",
    options=list(reversed(all_weeks)),
    index=len(all_weeks) - 1 - default_compare_idx,
    format_func=lambda d: d.strftime("%Y-%m-%d"),
    key="compare_week",
)

strip_data = analytics.curve_strip_snapshot(selected_week, compare_week, contract_range)
front_month_code = curves.contract_code(*strip_data["chain"][0])
front_spread_code = curves.combo_code(strip_data["chain"], 2)
st.caption(
    f"Front-month chain starts at {front_month_code} (nearest unexpired as of {selected_week.date()}), "
    f"{contract_range} contracts forward. Comparing {selected_week.date()} (orange) vs {compare_week.date()} (white)."
)

curve_cols = st.columns(3, gap="small")
for curve_col, structure_name in zip(curve_cols, ("1MS", "1MF", "1MDF")):
    with curve_col:
        sdf = strip_data["structures"][structure_name]
        if sdf.empty or sdf["current"].isna().all():
            st.info(f"No data available for {structure_name} across this contract range.")
            continue
        fig_s = go.Figure()
        fig_s.add_trace(go.Bar(x=sdf["label"], y=sdf["diff"], name="Change", marker_color="rgba(150,150,150,0.5)"))
        fig_s.add_trace(
            go.Scatter(x=sdf["label"], y=sdf["compare"], mode="lines+markers", name=str(compare_week.date()),
                       line=dict(color="#bbbbbb", width=2))
        )
        fig_s.add_trace(
            go.Scatter(x=sdf["label"], y=sdf["current"], mode="lines+markers", name=str(selected_week.date()),
                       line=dict(color="#ff7f0e", width=2))
        )
        fig_s.update_layout(
            title=f"RB - {structure_name}",
            height=330,
            margin=dict(t=45, b=25, l=45, r=10),
            yaxis_title="$/gal",
            legend=dict(orientation="h", y=1.16, x=0),
        )
        st.plotly_chart(fig_s, use_container_width=True, key=f"strip_{structure_name}")
        calculated_sources = pd.concat(
            [sdf["current_source"], sdf["compare_source"]], ignore_index=True
        ).dropna()
        calculated_sources = sorted(
            source for source in calculated_sources.unique() if source != "QH API"
        )
        if calculated_sources:
            st.caption(f"{structure_name} fallback used: {', '.join(calculated_sources)}.")

st.caption(
    "1MS = adjacent-month spread; 1MF[i] = 1MS[i] - 1MS[i+1]; "
    "1MDF[i] = 1MF[i] - 1MF[i+1]. QH combo values are used when available, "
    "with calculated values only filling missing API points. Bars = change between the two dates."
)

# ---------------------------------------------------------------- Bottom row
st.divider()
inventory_col, crack_col = st.columns(2, gap="large")

with inventory_col:
    st.subheader("Inventory Breakdown & Market Reaction")
    padd_df = data_mod.fetch_all_padd_stocks(start_date=full_start)

    bars = []
    total_val = stocks_df.loc[stocks_df["date"] == selected_week, "actual"]
    if not total_val.empty:
        bars.append({"region": "Total US", "stocks": float(total_val.iloc[0])})
    if not padd_df.empty:
        padd_row = padd_df.loc[padd_df["date"] == selected_week]
        if not padd_row.empty:
            for p in range(1, 6):
                col = f"PADD{p}"
                if col in padd_row.columns and pd.notna(padd_row.iloc[0][col]):
                    bars.append({"region": f"PADD{p}", "stocks": float(padd_row.iloc[0][col])})

    if bars:
        bar_df = pd.DataFrame(bars)
        fig_inv = go.Figure(go.Bar(x=bar_df["region"], y=bar_df["stocks"]))
        fig_inv.update_layout(height=240, margin=dict(t=10, b=10), yaxis_title="K BBL")
        st.plotly_chart(fig_inv, use_container_width=True, key="inv_fig")

    try:
        base_idx = all_weeks.index(selected_week)
        window_idx = range(max(0, base_idx - 2), min(len(all_weeks), base_idx + 3))
        table_rows = []
        for i in window_idx:
            wk = all_weeks[i]
            row = {"Week": wk.date().isoformat()}
            row["Total Draw/Build"] = stocks_df.iloc[i]["wow_change"] if i > 0 else None
            if not padd_df.empty:
                prow = padd_df.loc[padd_df["date"] == wk]
                prev_prow = padd_df.loc[padd_df["date"] == all_weeks[i - 1]] if i > 0 else None
                for p in range(1, 6):
                    col = f"PADD{p}"
                    if (
                        not prow.empty
                        and prev_prow is not None
                        and not prev_prow.empty
                        and col in prow.columns
                        and pd.notna(prow.iloc[0][col])
                        and pd.notna(prev_prow.iloc[0][col])
                    ):
                        row[f"PADD{p} Draw/Build"] = prow.iloc[0][col] - prev_prow.iloc[0][col]
            table_rows.append(row)
        st.dataframe(pd.DataFrame(table_rows).set_index("Week"), use_container_width=True)
    except ValueError:
        pass

    st.subheader("Release Reaction (+%d Min Window)" % REACTION_WINDOW_MINUTES)
    for reaction_label, reaction_code in (
        ("RB front month", front_month_code),
        ("RB 1MS", front_spread_code),
    ):
        st.markdown(f"**{reaction_label} — {reaction_code}**")
        reaction_result = reaction_mod.price_reaction(
            reaction_code, selected_week, REACTION_WINDOW_MINUTES
        )
        if reaction_result:
            r_cols = st.columns(3)
            r_cols[0].metric("Pre-release", f"{reaction_result['pre_price']:.4f}")
            r_cols[1].metric(
                "Post-release",
                f"{reaction_result['post_price']:.4f}",
                delta=f"{reaction_result['change']:+.4f}",
            )
            pct = reaction_result["pct_change"]
            r_cols[2].metric("% Move", f"{pct:+.2f}%" if pct is not None else "n/a")
            release_et = reaction_result["release_time_utc"].tz_convert("America/New_York")
            st.caption(
                f"Release at {release_et.strftime('%Y-%m-%d %H:%M %Z')}; "
                "holiday-adjusted EIA calendar."
            )
        else:
            st.info(f"No intraday (5-minute) data available for {reaction_code} in this release window.")

    st.subheader("Inventory Signal Strategy Backtest")
    bt_row_1 = st.columns(3)
    strategy_region = bt_row_1[0].selectbox(
        "Inventory signal",
        options=["Total US", "PADD1", "PADD2", "PADD3", "PADD4", "PADD5"],
        key="strategy_region",
    )
    strategy_lookback_months = int(
        bt_row_1[1].number_input(
            "Lookback (months)",
            min_value=1,
            max_value=24,
            value=6,
            step=1,
            key="strategy_lookback_months",
        )
    )
    holding_label = bt_row_1[2].selectbox(
        "Maximum holding period",
        options=["30 minutes", "1 hour", "2 hours", "4 hours", "6 hours"],
        index=1,
        key="strategy_holding_period",
    )
    holding_minutes = {
        "30 minutes": 30,
        "1 hour": 60,
        "2 hours": 120,
        "4 hours": 240,
        "6 hours": 360,
    }[holding_label]

    bt_row_2 = st.columns(3)
    stop_dollars = float(
        bt_row_2[0].number_input(
            "Dollar stop (per spread)",
            min_value=4.2,
            value=420.0,
            step=4.2,
            format="%.2f",
            key="strategy_stop_dollars",
        )
    )
    target_mode = bt_row_2[1].radio(
        "Target method",
        options=["Stop multiple", "Dollar target"],
        horizontal=True,
        key="strategy_target_mode",
    )
    if target_mode == "Stop multiple":
        target_value = float(
            bt_row_2[2].number_input(
                "Target (× stop)",
                min_value=0.25,
                value=2.0,
                step=0.25,
                key="strategy_target_multiple",
            )
        )
        target_dollars = stop_dollars * target_value
    else:
        target_value = float(
            bt_row_2[2].number_input(
                "Dollar target (per spread)",
                min_value=4.2,
                value=840.0,
                step=4.2,
                format="%.2f",
                key="strategy_target_dollars",
            )
        )
        target_dollars = target_value

    if strategy_region == "Total US":
        strategy_signal_df = stocks_df[["date", "wow_change"]].rename(
            columns={"wow_change": "inventory_change"}
        )
    elif not padd_df.empty and strategy_region in padd_df:
        strategy_signal_df = padd_df[["date", strategy_region]].sort_values("date").copy()
        strategy_signal_df["inventory_change"] = strategy_signal_df[strategy_region].diff()
        strategy_signal_df = strategy_signal_df[["date", "inventory_change"]]
    else:
        strategy_signal_df = pd.DataFrame()

    if strategy_signal_df.empty:
        st.info(f"No inventory history is available for {strategy_region}.")
    else:
        strategy_signal_rows = tuple(
            (row.date.isoformat(), float(row.inventory_change))
            for row in strategy_signal_df.dropna().itertuples(index=False)
        )
        with st.spinner("Backtesting RB 1MS release trades..."):
            strategy_results = analytics.backtest_inventory_signal_1ms(
                strategy_signal_rows,
                lookback_months=strategy_lookback_months,
                holding_minutes=holding_minutes,
                stop_dollars=stop_dollars,
                target_mode=target_mode,
                target_value=target_value,
            )

        if strategy_results.empty:
            st.info("No usable RB 1MS event bars were returned for this configuration.")
        else:
            total_pnl = strategy_results["pnl_dollars"].sum()
            average_pnl = strategy_results["pnl_dollars"].mean()
            win_rate = (strategy_results["pnl_dollars"] > 0).mean() * 100
            strategy_metrics = st.columns(4)
            strategy_metrics[0].metric("Total P&L", f"${total_pnl:,.0f}")
            strategy_metrics[1].metric("Average / Trade", f"${average_pnl:,.0f}")
            strategy_metrics[2].metric("Win Rate", f"{win_rate:.1f}%")
            strategy_metrics[3].metric("Trades", f"{len(strategy_results)}")

            fig_strategy = go.Figure()
            fig_strategy.add_trace(
                go.Bar(
                    x=strategy_results["week"],
                    y=strategy_results["pnl_dollars"],
                    name="Trade P&L",
                    marker_color=[
                        "#2ca02c" if value >= 0 else "#d62728"
                        for value in strategy_results["pnl_dollars"]
                    ],
                    customdata=strategy_results[
                        ["signal", "side", "inventory_change", "exit_reason"]
                    ],
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>"
                        "%{customdata[0]} → %{customdata[1]}<br>"
                        "Inventory change: %{customdata[2]:,.0f} K BBL<br>"
                        "Exit: %{customdata[3]}<br>"
                        "Trade P&L: $%{y:,.0f}<extra></extra>"
                    ),
                )
            )
            fig_strategy.add_trace(
                go.Scatter(
                    x=strategy_results["week"],
                    y=strategy_results["cumulative_pnl"],
                    mode="lines+markers",
                    name="Cumulative P&L",
                    yaxis="y2",
                    line=dict(color="#1f77b4", width=2),
                )
            )
            fig_strategy.update_layout(
                height=330,
                margin=dict(t=25, b=20),
                yaxis=dict(title="Trade P&L ($)"),
                yaxis2=dict(
                    title="Cumulative P&L ($)",
                    overlaying="y",
                    side="right",
                    showgrid=False,
                ),
                legend=dict(orientation="h", y=1.12),
            )
            st.plotly_chart(
                fig_strategy,
                use_container_width=True,
                key="inventory_signal_strategy_chart",
            )

            strategy_table = strategy_results.copy()
            strategy_table["week"] = strategy_table["week"].dt.strftime("%Y-%m-%d")
            strategy_table["entry_time"] = (
                strategy_table["entry_time"]
                .dt.tz_convert("America/New_York")
                .dt.strftime("%Y-%m-%d %H:%M")
            )
            strategy_table["exit_time"] = (
                strategy_table["exit_time"]
                .dt.tz_convert("America/New_York")
                .dt.strftime("%Y-%m-%d %H:%M")
            )
            strategy_table = strategy_table.rename(
                columns={
                    "week": "Release",
                    "signal": "Signal",
                    "side": "Side",
                    "inventory_change": "Inventory Change (K BBL)",
                    "spread_code": "RB 1MS",
                    "entry_time": "Entry (ET)",
                    "exit_time": "Exit (ET)",
                    "entry_price": "Entry",
                    "exit_price": "Exit",
                    "exit_reason": "Exit Reason",
                    "pnl_dollars": "P&L ($)",
                }
            )
            st.dataframe(
                strategy_table[
                    [
                        "Release",
                        "Signal",
                        "Side",
                        "Inventory Change (K BBL)",
                        "RB 1MS",
                        "Entry (ET)",
                        "Exit (ET)",
                        "Entry",
                        "Exit",
                        "Exit Reason",
                        "P&L ($)",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.caption(
            f"One RB 1MS spread: 0.0001 tick = \\$4.20. Draws enter long; builds enter short "
            f"at the first 5-minute bar open after release. Stop \\${stop_dollars:,.2f}; "
            f"target \\${target_dollars:,.2f}; maximum hold {holding_label.lower()}. "
            "If stop and target trade within the same bar, the stop is applied first."
        )

with crack_col:
    st.subheader("Gasoline Crack Spread Context")
    crack_span = st.slider("Weeks each side of selected week", 2, 4, 3, key="crack_span")
    crack_df = analytics.crack_series(selected_week, crack_span, crack_span, all_weeks)
    if crack_df.empty or crack_df["crack"].isna().all():
        st.info("No crack spread data available for this window.")
    else:
        fig_crack = go.Figure(
            go.Scatter(x=crack_df["week"], y=crack_df["crack"], mode="lines+markers", name="RB-CL crack ($/bbl)")
        )
        fig_crack.add_vline(x=selected_week, line_dash="dash", line_color="gray")
        fig_crack.update_layout(height=260, margin=dict(t=10, b=10), yaxis_title="$/bbl")
        st.plotly_chart(fig_crack, use_container_width=True, key="crack_fig")
        st.caption(
            "Front-month RB-CL crack spread (RBCL<contract>), re-resolved per week so contract rolls "
            "don't create artificial jumps. Dashed line marks the selected week."
        )

    st.subheader("PADD Draw/Build vs RB 1MS One-Hour Move")
    correlation_lookback = st.selectbox(
        "Correlation lookback (EIA releases)",
        options=[13, 26, 48],
        index=1,
        key="correlation_lookback",
    )
    with st.spinner("Loading historical one-hour RB 1MS reactions..."):
        reaction_history = analytics.historical_1ms_reactions(
            all_weeks,
            lookback_weeks=correlation_lookback,
            window_minutes=REACTION_WINDOW_MINUTES,
        )
    if reaction_history.empty or reaction_history["move_1h"].isna().all():
        st.info("No historical RB 1MS release reactions available for this lookback.")
    else:
        inventory_changes = stocks_df[["date", "wow_change"]].rename(
            columns={"wow_change": "Total US"}
        )
        if not padd_df.empty:
            padd_changes = padd_df.sort_values("date").copy()
            for padd in range(1, 6):
                col = f"PADD{padd}"
                if col in padd_changes:
                    padd_changes[col] = padd_changes[col].diff()
            inventory_changes = inventory_changes.merge(
                padd_changes, on="date", how="left"
            )

        corr_df = inventory_changes.merge(
            reaction_history,
            left_on="date",
            right_on="week",
            how="inner",
        )
        correlation_rows = []
        for region in ["Total US", "PADD1", "PADD2", "PADD3", "PADD4", "PADD5"]:
            if region not in corr_df:
                continue
            paired = corr_df[[region, "move_1h"]].dropna()
            # Trading convention: inventory tightening is positive. Therefore a
            # draw (negative raw EIA change) becomes positive, while a build
            # becomes negative. A positive correlation then reads intuitively as
            # larger draw/tighter stocks -> stronger (upward) RB 1MS.
            inventory_tightness = -paired[region]
            avg_inventory_change = paired[region].mean() if not paired.empty else None
            avg_spread_move = paired["move_1h"].mean() if not paired.empty else None
            draw_up = int(((paired[region] < 0) & (paired["move_1h"] > 0)).sum())
            draw_down = int(((paired[region] < 0) & (paired["move_1h"] < 0)).sum())
            build_down = int(((paired[region] > 0) & (paired["move_1h"] < 0)).sum())
            build_up = int(((paired[region] > 0) & (paired["move_1h"] > 0)).sum())
            direction_matches = draw_up + build_down
            direction_misses = draw_down + build_up
            directional_observations = direction_matches + direction_misses
            directional_hit_rate = (
                direction_matches / directional_observations * 100
                if directional_observations
                else None
            )
            if pd.notna(avg_inventory_change):
                inventory_direction = "build" if avg_inventory_change >= 0 else "draw"
                avg_inventory = f"{abs(avg_inventory_change):,.0f} K BBL {inventory_direction}"
            else:
                avg_inventory = "n/a"
            correlation_rows.append(
                {
                    "region": region,
                    "correlation": inventory_tightness.corr(paired["move_1h"]),
                    "observations": len(paired),
                    "avg_inventory": avg_inventory,
                    "avg_spread_move": (
                        f"{avg_spread_move:+.4f}" if pd.notna(avg_spread_move) else "n/a"
                    ),
                    "directional_hit_rate": directional_hit_rate,
                    "direction_matches": direction_matches,
                    "direction_misses": direction_misses,
                    "draw_up": draw_up,
                    "draw_down": draw_down,
                    "build_down": build_down,
                    "build_up": build_up,
                }
            )

        correlation_df = pd.DataFrame(correlation_rows)
        fig_corr = go.Figure(
            go.Bar(
                x=correlation_df["region"],
                y=correlation_df["correlation"],
                customdata=correlation_df[
                    ["observations", "avg_inventory", "avg_spread_move"]
                ],
                text=correlation_df["correlation"].map(
                    lambda value: f"{value:.2f}" if pd.notna(value) else "n/a"
                ),
                textposition="outside",
                marker_color=[
                    "#2ca02c" if pd.notna(value) and value >= 0 else "#d62728"
                    for value in correlation_df["correlation"]
                ],
                hovertemplate=(
                    "%{x}<br>Pearson r: %{y:.3f}<br>"
                    "Observations: %{customdata[0]}<br>"
                    "Average inventory: %{customdata[1]}<br>"
                    "Average 1MS move: %{customdata[2]} $/gal"
                    "<extra></extra>"
                ),
            )
        )
        fig_corr.add_hline(y=0, line_color="gray", line_width=1)
        fig_corr.update_layout(
            height=300,
            margin=dict(t=20, b=20),
            yaxis=dict(title="Correlation (draw positive)", range=[-1, 1]),
        )
        st.plotly_chart(fig_corr, use_container_width=True, key="padd_1ms_correlation")
        valid_reactions = reaction_history["move_1h"].notna().sum()
        st.caption(
            f"Trading-sign correlation: inventory draws are positive and builds are negative, "
            f"compared with the signed RB 1MS price change from release time to +{REACTION_WINDOW_MINUTES} minutes. "
            f"Green means larger draws align with a stronger 1MS; red means the relationship is inverse. "
            f"{valid_reactions} of {len(reaction_history)} requested releases had usable intraday data."
        )

        st.subheader("Directional Consistency: Draw → 1MS Up / Build → 1MS Down")
        fig_direction = go.Figure(
            go.Bar(
                x=correlation_df["region"],
                y=correlation_df["directional_hit_rate"],
                customdata=correlation_df[
                    [
                        "direction_matches",
                        "direction_misses",
                        "draw_up",
                        "draw_down",
                        "build_down",
                        "build_up",
                    ]
                ],
                text=correlation_df["directional_hit_rate"].map(
                    lambda value: f"{value:.0f}%" if pd.notna(value) else "n/a"
                ),
                textposition="inside",
                insidetextanchor="end",
                textfont=dict(color="white"),
                marker_color=[
                    "#2ca02c" if pd.notna(value) and value >= 50 else "#d62728"
                    for value in correlation_df["directional_hit_rate"]
                ],
                hovertemplate=(
                    "%{x}<br>Directional success: %{y:.1f}%<br>"
                    "Matches: %{customdata[0]}<br>"
                    "Opposite: %{customdata[1]}<br>"
                    "Draw → 1MS up: %{customdata[2]}<br>"
                    "Draw → 1MS down: %{customdata[3]}<br>"
                    "Build → 1MS down: %{customdata[4]}<br>"
                    "Build → 1MS up: %{customdata[5]}"
                    "<extra></extra>"
                ),
            )
        )
        fig_direction.add_hline(
            y=50,
            line_color="gray",
            line_dash="dash",
        )
        fig_direction.update_layout(
            height=300,
            margin=dict(t=25, b=20),
            yaxis=dict(title="Directional success rate", range=[0, 105], ticksuffix="%"),
        )
        st.plotly_chart(
            fig_direction,
            use_container_width=True,
            key="padd_1ms_directional_consistency",
        )
        st.caption(
            f"Uses the same {correlation_lookback}-release lookback selected above. "
            "Green is at least 50% directional agreement; hover for the draw/build outcome counts."
        )
