#!/usr/bin/env python3
"""
rain_delay.py — Check Open-Meteo forecast and enable/disable sprinklers_pi scheduling.

Designed to run as a cron job the evening before each scheduled watering day.
Communicates directly with the sprinklers_pi web API to toggle the "Run Schedules" switch.
No API key required — Open-Meteo is free and open.
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import requests
import yaml


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging(log_file: str) -> logging.Logger:
    logger = logging.getLogger("rain_delay")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# sprinklers_pi API
# ---------------------------------------------------------------------------

def sprinkler_get_status(base_url: str) -> str:
    r = requests.get(f"{base_url}/json/state", timeout=10)
    r.raise_for_status()
    return r.json().get("run", "unknown")


def sprinkler_set_run(base_url: str, enable: bool) -> None:
    value = "on" if enable else "off"
    r = requests.get(f"{base_url}/bin/run", params={"system": value}, timeout=10)
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Open-Meteo forecast  (free, no API key)
# ---------------------------------------------------------------------------

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def get_forecast(lat: float, lon: float) -> dict:
    """Fetch hourly precipitation probability and accumulation from Open-Meteo."""
    r = requests.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation_probability,precipitation",
            "precipitation_unit": "inch",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def evaluate_forecast(forecast: dict, lookahead_hours: int) -> tuple[float, float]:
    """Return (max_probability_pct, total_accumulation_inches) over the next lookahead_hours."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=lookahead_hours)

    hourly = forecast["hourly"]
    max_prob = 0.0
    total_accum = 0.0

    for time_str, prob, accum in zip(
        hourly["time"],
        hourly["precipitation_probability"],
        hourly["precipitation"],
    ):
        # Open-Meteo times are local (timezone=auto), parse as naive then compare window
        slot = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
        if slot < now or slot > cutoff:
            continue
        if prob is not None:
            max_prob = max(max_prob, float(prob))
        if accum is not None:
            total_accum += float(accum)

    return max_prob, total_accum


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def next_watering_day(watering_days: list[str], watering_hour: int) -> datetime:
    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    now = datetime.now()
    target_dow = {day_map[d.lower()] for d in watering_days}

    for offset in range(1, 8):
        candidate = now + timedelta(days=offset)
        if candidate.weekday() in target_dow:
            return candidate.replace(hour=watering_hour, minute=0, second=0, microsecond=0)

    raise RuntimeError("Could not determine next watering day")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sprinkler rain delay checker")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Log decision but don't change sprinkler state")
    parser.add_argument("--status", action="store_true", help="Print current sprinkler run status and exit")
    args = parser.parse_args()

    cfg = load_config(args.config)
    logger = setup_logging(cfg["log_file"])
    base_url = cfg["sprinkler_pi"]["base_url"].rstrip("/")

    if args.status:
        status = sprinkler_get_status(base_url)
        logger.info("Sprinkler run schedules: %s", status)
        return

    try:
        next_run = next_watering_day(cfg["watering_days"], cfg["watering_hour"])
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)

    lookahead = cfg["thresholds"]["lookahead_hours"]
    lat = cfg["location"]["lat"]
    lon = cfg["location"]["lon"]

    logger.info(
        "Next watering: %s | Checking %dh Open-Meteo forecast for %.4f, %.4f",
        next_run.strftime("%A %Y-%m-%d %H:%M"), lookahead, lat, lon,
    )

    try:
        current_status = sprinkler_get_status(base_url)
        logger.info("Current sprinkler run schedules: %s", current_status)
    except requests.RequestException as e:
        logger.error("Failed to reach sprinklers_pi at %s: %s", base_url, e)
        sys.exit(1)

    try:
        forecast = get_forecast(lat, lon)
    except requests.RequestException as e:
        logger.error("Failed to fetch Open-Meteo forecast: %s", e)
        sys.exit(1)

    max_prob, total_accum = evaluate_forecast(forecast, lookahead)
    prob_thresh = cfg["thresholds"]["rain_probability_pct"]
    accum_thresh = cfg["thresholds"]["accumulation_inches"]

    logger.info(
        'Forecast: %.0f%% rain probability, %.2f" expected accumulation '
        '(thresholds: %d%% / %.2f")',
        max_prob, total_accum, prob_thresh, accum_thresh,
    )

    should_skip = max_prob >= prob_thresh and total_accum >= accum_thresh

    if should_skip:
        logger.info(
            'DECISION: SKIP — disabling sprinkler schedules '
            '(%.0f%% >= %d%% AND %.2f" >= %.2f")',
            max_prob, prob_thresh, total_accum, accum_thresh,
        )
        if not args.dry_run:
            sprinkler_set_run(base_url, enable=False)
            logger.info("Sprinkler run schedules set to OFF")
    else:
        logger.info("DECISION: RUN — enabling sprinkler schedules (conditions below threshold)")
        if not args.dry_run:
            sprinkler_set_run(base_url, enable=True)
            logger.info("Sprinkler run schedules set to ON")

    if args.dry_run:
        logger.info("(dry-run: no changes made)")


if __name__ == "__main__":
    main()
