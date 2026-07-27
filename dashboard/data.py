from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pandas as pd
import streamlit as st

import eia_calendar
from qh_api import QHAPIClient, QHAPIError

GASOLINE_STOCKS_QHCODE = "US Gasoline Stocks_1W"
PADD_STOCKS_QHCODES = {
    1: "US PADD1 Gasoline Stocks_1W",
    2: "US PADD2 Gasoline Stocks_1W",
    3: "US PADD3 Gasoline Stocks_1W",
    4: "US PADD4 Gasoline Stocks_1W",
    5: "US PADD5 Gasoline Stocks_1W",
}


@st.cache_resource
def get_client() -> QHAPIClient:
    client = QHAPIClient()
    client.authenticate()
    return client


def _paginate(client: QHAPIClient, path: str, params: dict) -> list[dict]:
    rows: list[dict] = []
    next_path, next_params = path, dict(params)
    while True:
        data = client.request("GET", next_path, params=next_params)
        rows.extend(data.get("results", []))
        nxt = data.get("next")
        if not nxt:
            break
        parsed = urlparse(nxt)
        next_path = parsed.path
        next_params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fundamental_series(qhcode: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    client = get_client()
    params = {"qhcode": qhcode, "count": 500, "start_date": start_date}
    if end_date:
        params["end_date"] = end_date
    rows = _paginate(client, "/api/fundamentaldata/", params)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    # QH labels EIA weekly observations with Friday. Align every weekly series
    # to its actual EIA publication date, including holiday-delayed releases.
    df["date"] = df["date"].map(eia_calendar.release_date_from_qh_label)
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    return df[["date", "actual"]].drop_duplicates("date").sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_all_padd_stocks(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    frames = []
    for padd, qhcode in PADD_STOCKS_QHCODES.items():
        df = fetch_fundamental_series(qhcode, start_date, end_date)
        if df.empty:
            continue
        df = df.rename(columns={"actual": f"PADD{padd}"}).set_index("date")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).reset_index()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_ohlc_v2(instruments: tuple[str, ...], interval: str, count: int | None = None,
                   start: int | None = None, end: int | None = None) -> pd.DataFrame:
    client = get_client()
    data = None
    for attempt in range(3):
        try:
            data = client.get_ohlc_v2(
                instruments=list(instruments),
                interval=interval,
                count=count,
                start=start,
                end=end,
            )
            break
        except QHAPIError:
            if attempt == 2:
                # Streamlit does not cache raised exceptions, so a later rerun
                # can retry this window instead of preserving a false empty hit.
                raise
            time.sleep(0.4 * (2**attempt))
    items = data.get("data") if isinstance(data, dict) else data
    df = pd.DataFrame(items or [])
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df.sort_values("time").reset_index(drop=True)
