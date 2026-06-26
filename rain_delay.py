#!/usr/bin/env python3
"""
rain_delay.py — ET₀ soil water balance irrigation delay.

Runs as a cron job before each scheduled watering day. Uses Open-Meteo's
FAO-56 Penman-Monteith ET₀ to estimate soil moisture and decide whether
to enable or disable the sprinklers_pi run schedules switch.
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime

import requests
import yaml

from water_balance import run_balance

DISCORD_API = "https://discord.com/api/v10"


def discord_post(channel_id: int, content: str) -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        try:
            with open("/run/secrets/bot_token") as f:
                token = f.read().strip()
        except FileNotFoundError:
            return
    requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {token}"},
        json={"content": content},
        timeout=10,
    )

STATE_FILE = "/app/delay_state.json"


def active_manual_delay() -> datetime | None:
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        raw = state.get("delay_until")
        if raw:
            until = datetime.fromisoformat(raw)
            return until if until > datetime.now() else None
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging(log_file: str) -> logging.Logger:
    logger = logging.getLogger("rain_delay")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    for h in [logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)]:
        h.setFormatter(fmt)
        logger.addHandler(h)
    return logger


def sprinkler_get_status(base_url: str) -> str:
    r = requests.get(f"{base_url}/json/state", timeout=10)
    r.raise_for_status()
    return r.json().get("run", "unknown")


def sprinkler_set_run(base_url: str, enable: bool) -> None:
    r = requests.get(f"{base_url}/bin/run", params={"system": "on" if enable else "off"}, timeout=10)
    r.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    logger = setup_logging(cfg["log_file"])
    base_url = cfg["sprinkler_pi"]["base_url"].rstrip("/")

    if args.status:
        logger.info("Sprinkler run schedules: %s", sprinkler_get_status(base_url))
        return

    verbose = cfg.get("verbose", False)
    channel_id = cfg["discord"]["channel_id"]

    delay_until = active_manual_delay()
    if delay_until:
        msg = f"Manual delay active until **{delay_until.strftime('%a %b %-d')}** — skipping auto-check"
        logger.info(msg)
        if verbose:
            discord_post(channel_id, f"⏳ {msg}")
        return

    wb = cfg["water_balance"]
    logger.info(
        "Running ET₀ water balance | field capacity: %.0f mm | threshold: %.0f mm | "
        "lookback: %d days",
        wb["field_capacity_mm"], wb["watering_threshold_mm"], wb["lookback_days"],
    )

    try:
        current_soil, projections = run_balance(cfg)
    except requests.RequestException as e:
        logger.error("Failed to fetch Open-Meteo data: %s", e)
        sys.exit(1)

    logger.info("Estimated soil moisture: %.1f / %.0f mm", current_soil, wb["field_capacity_mm"])

    try:
        logger.info("Current sprinkler run schedules: %s", sprinkler_get_status(base_url))
    except requests.RequestException as e:
        logger.error("Failed to reach sprinklers_pi at %s: %s", base_url, e)
        sys.exit(1)

    # Log full week projection
    for p in projections:
        if p.is_watering_day:
            action = "SKIP" if p.skip else "RUN "
            logger.info("  %s %s — soil %.1f mm | rain %.1f mm | ET₀ %.1f mm",
                        p.date.strftime("%a %b %-d"), action, p.soil_mm, p.rain_mm, p.et0_mm)

    # Today's decision
    today = date.today()
    today_p = next((p for p in projections if p.date == today and p.is_watering_day), None)

    if today_p is None:
        logger.info("Today is not a scheduled watering day — no action taken")
        return

    if today_p.skip:
        logger.info(
            "DECISION: SKIP — soil %.1f mm >= threshold %.0f mm",
            today_p.soil_mm, wb["watering_threshold_mm"],
        )
        if not args.dry_run:
            sprinkler_set_run(base_url, enable=False)
            logger.info("Sprinkler run schedules set to OFF")
        decision_line = (
            f"⛔ **SKIP** — soil {today_p.soil_mm:.1f} mm ≥ threshold {wb['watering_threshold_mm']:.0f} mm"
        )
    else:
        logger.info(
            "DECISION: RUN — soil %.1f mm < threshold %.0f mm",
            today_p.soil_mm, wb["watering_threshold_mm"],
        )
        if not args.dry_run:
            sprinkler_set_run(base_url, enable=True)
            logger.info("Sprinkler run schedules set to ON")
        decision_line = (
            f"💧 **RUN** — soil {today_p.soil_mm:.1f} mm < threshold {wb['watering_threshold_mm']:.0f} mm"
        )

    if args.dry_run:
        logger.info("(dry-run: no changes made)")

    if verbose:
        cap = wb["field_capacity_mm"]
        proj_lines = []
        for p in projections:
            if p.is_watering_day:
                icon = "⛔" if p.skip else "💧"
                action = "SKIP" if p.skip else "RUN "
                proj_lines.append(
                    f"{icon} **{p.date.strftime('%a %b %-d')}** — {action} "
                    f"| soil {p.soil_mm:.1f} mm | rain {p.rain_mm:.1f} mm | ET₀ {p.et0_mm:.1f} mm"
                )
        msg = "\n".join([
            f"🌱 **Rain delay check — {today.strftime('%a %b %-d')}**",
            f"Est. soil moisture: **{current_soil:.1f} / {cap:.0f} mm**",
            decision_line,
            "",
            "**Upcoming watering days:**",
        ] + (proj_lines or ["_None in forecast window_"]))
        discord_post(channel_id, msg)


if __name__ == "__main__":
    main()
