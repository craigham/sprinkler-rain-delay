#!/usr/bin/env python3
"""
Discord bot for sprinkler-rain-delay.
Slash commands: /status  /delay <days>  /cancel  /forecast
"""

import json
import os
from datetime import datetime, timedelta, timezone

import discord
import requests
import yaml
from discord import app_commands

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.yaml")
STATE_FILE = "/app/delay_state.json"

with open(CONFIG_PATH) as f:
    cfg = yaml.safe_load(f)

GUILD_ID = cfg["discord"]["guild_id"]
BASE_URL = cfg["sprinkler_pi"]["base_url"].rstrip("/")
LAT = cfg["location"]["lat"]
LON = cfg["location"]["lon"]
PROB_THRESH = cfg["thresholds"]["rain_probability_pct"]
ACCUM_THRESH = cfg["thresholds"]["accumulation_inches"]


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def read_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def clear_state() -> None:
    try:
        os.remove(STATE_FILE)
    except FileNotFoundError:
        pass


def active_manual_delay() -> datetime | None:
    """Return delay expiry if a manual delay is currently active, else None."""
    state = read_state()
    raw = state.get("delay_until")
    if not raw:
        return None
    until = datetime.fromisoformat(raw)
    return until if until > datetime.now() else None


# ---------------------------------------------------------------------------
# sprinklers_pi helpers
# ---------------------------------------------------------------------------

def sprinkler_status() -> str:
    r = requests.get(f"{BASE_URL}/json/state", timeout=10)
    r.raise_for_status()
    return r.json().get("run", "unknown")


def sprinkler_set(enable: bool) -> None:
    r = requests.get(f"{BASE_URL}/bin/run", params={"system": "on" if enable else "off"}, timeout=10)
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Open-Meteo helpers
# ---------------------------------------------------------------------------

def fetch_forecast() -> tuple[float, float]:
    """Return (max_precip_probability_pct, total_accumulation_inches) for next 24h."""
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": LAT,
            "longitude": LON,
            "hourly": "precipitation_probability,precipitation",
            "precipitation_unit": "inch",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    hourly = data["hourly"]

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    max_prob, total_accum = 0.0, 0.0

    for time_str, prob, accum in zip(
        hourly["time"],
        hourly["precipitation_probability"],
        hourly["precipitation"],
    ):
        slot = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
        if slot < now or slot > cutoff:
            continue
        if prob is not None:
            max_prob = max(max_prob, float(prob))
        if accum is not None:
            total_accum += float(accum)

    return max_prob, total_accum


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
GUILD = discord.Object(id=GUILD_ID)


@client.event
async def on_ready():
    await tree.sync(guild=GUILD)
    print(f"Logged in as {client.user} | commands synced to guild {GUILD_ID}")


@tree.command(name="status", description="Show sprinkler controller status and any active delay", guild=GUILD)
async def cmd_status(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        run = sprinkler_status()
    except Exception as e:
        await interaction.followup.send(f"❌ Could not reach sprinklers_pi: {e}")
        return

    emoji = "💧" if run == "on" else "⛔"
    lines = [f"{emoji} Schedules are **{run.upper()}**"]

    delay_until = active_manual_delay()
    if delay_until:
        lines.append(f"⏳ Manual delay active until **{delay_until.strftime('%a %b %-d')}**")

    await interaction.followup.send("\n".join(lines))


@tree.command(name="delay", description="Manually skip watering for N days (1–14)", guild=GUILD)
@app_commands.describe(days="Number of days to pause watering")
async def cmd_delay(interaction: discord.Interaction, days: int):
    if not 1 <= days <= 14:
        await interaction.response.send_message("❌ Days must be between 1 and 14.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        sprinkler_set(enable=False)
    except Exception as e:
        await interaction.followup.send(f"❌ Could not reach sprinklers_pi: {e}")
        return

    until = datetime.now() + timedelta(days=days)
    write_state({"delay_until": until.isoformat(), "days": days, "source": "discord"})

    day_word = "day" if days == 1 else "days"
    await interaction.followup.send(
        f"⛔ Watering paused for **{days} {day_word}**. "
        f"Schedules resume **{until.strftime('%a %b %-d')}**."
    )


@tree.command(name="cancel", description="Cancel any active delay and re-enable watering", guild=GUILD)
async def cmd_cancel(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        sprinkler_set(enable=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Could not reach sprinklers_pi: {e}")
        return

    clear_state()
    await interaction.followup.send("💧 Delay cancelled — watering schedules are **ON**.")


@tree.command(name="forecast", description="Show next 24h rain forecast for your location", guild=GUILD)
async def cmd_forecast(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        max_prob, total_accum = fetch_forecast()
    except Exception as e:
        await interaction.followup.send(f"❌ Could not fetch forecast: {e}")
        return

    will_skip = max_prob >= PROB_THRESH and total_accum >= ACCUM_THRESH
    decision = "⛔ would **skip** watering" if will_skip else "💧 would **run** watering"

    await interaction.followup.send(
        f"🌦️ **Next 24h forecast** (Open-Meteo)\n"
        f"• Rain probability: **{max_prob:.0f}%** (threshold: {PROB_THRESH}%)\n"
        f"• Accumulation: **{total_accum:.2f}\"** (threshold: {ACCUM_THRESH}\")\n"
        f"• Auto-delay {decision}"
    )


if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        secret = "/run/secrets/bot_token"
        with open(secret) as f:
            token = f.read().strip()
    client.run(token)
