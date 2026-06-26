"""
ET₀-based soil water balance for irrigation scheduling.

Uses Open-Meteo's FAO-56 Penman-Monteith ET₀ (free, no API key) alongside
precipitation to maintain a virtual soil moisture tank and project watering
decisions forward across upcoming scheduled days.

Algorithm (FAO-56 water balance):
    soil_moisture += daily_rain - daily_ET₀
    clamped to [0, field_capacity_mm]

Decision: skip watering if projected soil moisture >= watering_threshold_mm.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class DayProjection:
    date: date
    soil_mm: float          # estimated soil moisture at start of day (before watering)
    rain_mm: float          # forecast precipitation
    et0_mm: float           # forecast reference evapotranspiration
    is_watering_day: bool
    skip: bool              # True = skip watering (soil is wet enough)

    @property
    def net_mm(self) -> float:
        return self.rain_mm - self.et0_mm


def fetch_daily(lat: float, lon: float, lookback_days: int, forecast_days: int) -> dict:
    """Fetch historical + forecast daily precipitation and ET₀ from Open-Meteo."""
    r = requests.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "precipitation_sum,et0_fao_evapotranspiration",
            "past_days": lookback_days,
            "forecast_days": forecast_days,
            "timezone": "auto",
            "precipitation_unit": "mm",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["daily"]


def reconstruct_soil_moisture(daily: dict, field_capacity_mm: float) -> tuple[float, list[dict]]:
    """
    Walk historical data to estimate current soil moisture, then return
    remaining (future) daily entries from today onward.

    Starts at field_capacity — assumes soil was saturated at the beginning of
    the lookback window (conservative for Vancouver's wet climate).
    """
    today = date.today()
    soil = float(field_capacity_mm)
    future = []

    for date_str, rain, et0 in zip(
        daily["time"],
        daily["precipitation_sum"],
        daily["et0_fao_evapotranspiration"],
    ):
        d = date.fromisoformat(date_str)
        rain = float(rain or 0.0)
        et0 = float(et0 or 0.0)

        if d < today:
            soil = max(0.0, min(field_capacity_mm, soil + rain - et0))
        else:
            future.append({"date": d, "rain_mm": rain, "et0_mm": et0})

    return soil, future


def project(
    current_soil_mm: float,
    future: list[dict],
    field_capacity_mm: float,
    watering_threshold_mm: float,
    watering_depth_mm: float,
    watering_days: list[str],
) -> list[DayProjection]:
    """
    Simulate soil moisture forward through future days.

    Watering is modelled as adding watering_depth_mm on days where the
    sprinkler runs (i.e. soil was below threshold). This keeps projections
    realistic past the first few days.

    Returns DayProjection for every future day (not just watering days).
    """
    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    watering_dow = {day_map[d.lower()] for d in watering_days}

    soil = current_soil_mm
    result = []

    for entry in future:
        d = entry["date"]
        rain = entry["rain_mm"]
        et0 = entry["et0_mm"]
        is_watering = d.weekday() in watering_dow

        # Decision: skip if soil is wet enough *before* rain falls today
        skip = is_watering and soil >= watering_threshold_mm

        result.append(DayProjection(
            date=d,
            soil_mm=soil,
            rain_mm=rain,
            et0_mm=et0,
            is_watering_day=is_watering,
            skip=skip,
        ))

        # Apply watering (if running today), then today's rain and ET₀
        if is_watering and not skip:
            soil = min(field_capacity_mm, soil + watering_depth_mm)
        soil = max(0.0, min(field_capacity_mm, soil + rain - et0))

    return result


def run_balance(cfg: dict) -> tuple[float, list[DayProjection]]:
    """
    Convenience wrapper: fetch data and return (current_soil_mm, projections).
    Call this from both rain_delay.py and bot.py.
    """
    wb = cfg["water_balance"]
    daily = fetch_daily(
        cfg["location"]["lat"],
        cfg["location"]["lon"],
        lookback_days=wb["lookback_days"],
        forecast_days=wb["forecast_days"],
    )
    current_soil, future = reconstruct_soil_moisture(daily, wb["field_capacity_mm"])
    projections = project(
        current_soil,
        future,
        field_capacity_mm=wb["field_capacity_mm"],
        watering_threshold_mm=wb["watering_threshold_mm"],
        watering_depth_mm=wb["watering_depth_mm"],
        watering_days=cfg["watering_days"],
    )
    return current_soil, projections
