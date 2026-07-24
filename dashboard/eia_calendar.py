from __future__ import annotations

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

# Exact alternate releases published by EIA. Keys are QH's Friday label for the
# week containing the release; values are (release date, hour ET, minute ET).
# These overrides also cover exceptional timings that cannot be inferred from
# the weekday alone.
_PUBLISHED_OVERRIDES: dict[pd.Timestamp, tuple[pd.Timestamp, int, int]] = {
    pd.Timestamp("2025-01-03"): (pd.Timestamp("2025-01-02"), 11, 0),
    pd.Timestamp("2025-01-24"): (pd.Timestamp("2025-01-23"), 12, 0),
    pd.Timestamp("2025-02-21"): (pd.Timestamp("2025-02-20"), 12, 0),
    pd.Timestamp("2025-05-30"): (pd.Timestamp("2025-05-29"), 12, 0),
    pd.Timestamp("2025-09-05"): (pd.Timestamp("2025-09-04"), 12, 0),
    pd.Timestamp("2025-10-17"): (pd.Timestamp("2025-10-16"), 12, 0),
    pd.Timestamp("2025-11-14"): (pd.Timestamp("2025-11-13"), 12, 0),
    pd.Timestamp("2026-01-02"): (pd.Timestamp("2025-12-29"), 17, 0),
    pd.Timestamp("2026-01-23"): (pd.Timestamp("2026-01-22"), 12, 0),
    pd.Timestamp("2026-02-20"): (pd.Timestamp("2026-02-19"), 12, 0),
    pd.Timestamp("2026-05-29"): (pd.Timestamp("2026-05-28"), 12, 0),
    pd.Timestamp("2026-09-11"): (pd.Timestamp("2026-09-10"), 12, 0),
    pd.Timestamp("2026-10-16"): (pd.Timestamp("2026-10-15"), 12, 0),
    pd.Timestamp("2026-11-13"): (pd.Timestamp("2026-11-12"), 12, 0),
}

_RELEASE_TIME_OVERRIDES = {
    release_date: (hour, minute)
    for release_date, hour, minute in _PUBLISHED_OVERRIDES.values()
}

_FEDERAL_HOLIDAYS = set(
    USFederalHolidayCalendar()
    .holidays(start="2000-01-01", end="2040-12-31")
    .normalize()
)


def release_date_from_qh_label(qh_friday: pd.Timestamp) -> pd.Timestamp:
    """Map QH's Friday weekly label to the actual EIA publication date."""
    qh_friday = pd.Timestamp(qh_friday).normalize()
    override = _PUBLISHED_OVERRIDES.get(qh_friday)
    if override:
        return override[0]

    release_date = qh_friday - pd.Timedelta(days=2)  # Friday -> Wednesday
    week_monday = release_date - pd.Timedelta(days=2)
    holiday_before_or_on_release = any(
        day in _FEDERAL_HOLIDAYS
        for day in pd.date_range(week_monday, release_date, freq="D")
    )
    if holiday_before_or_on_release:
        release_date += pd.Timedelta(days=1)
    return release_date


def release_datetime_utc(release_date: pd.Timestamp) -> pd.Timestamp:
    """Attach the EIA publication time and return a UTC timestamp."""
    release_date = pd.Timestamp(release_date).normalize()
    if release_date in _RELEASE_TIME_OVERRIDES:
        hour, minute = _RELEASE_TIME_OVERRIDES[release_date]
    elif release_date.weekday() == 3:  # generic holiday-delayed Thursday
        hour, minute = 12, 0
    else:
        hour, minute = 10, 30

    release_et = release_date.replace(hour=hour, minute=minute)
    return release_et.tz_localize("America/New_York").tz_convert("UTC")
