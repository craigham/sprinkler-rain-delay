# sprinkler-rain-delay

A cron-driven rain delay tool for sprinkler systems. Checks Weather Underground forecasts and sets a skip flag before scheduled watering runs.

## How it works

A cron job runs the evening before each scheduled watering day. It fetches an hourly forecast from Weather Underground using a Personal Weather Station (PWS), and if both the rain probability **and** expected accumulation exceed configured thresholds, it writes a skip flag file. Your sprinkler Pi checks for that flag before starting.

## Setup

```bash
pip install -r requirements.txt
cp config.yaml config.local.yaml   # edit with your WU API key; config.local.yaml is gitignored
```

Get a free Weather Underground API key at https://www.wunderground.com/member/api-keys

## Configuration

Edit `config.yaml` (or `config.local.yaml`):

| Key | Description |
|-----|-------------|
| `wu_api_key` | Your Weather Underground API key |
| `wu_station_id` | PWS station ID (e.g. `IVANCO101`) |
| `thresholds.rain_probability_pct` | Skip if forecast probability ≥ this value (default: 60) |
| `thresholds.accumulation_inches` | Skip if expected rain ≥ this value in inches (default: 0.25) |
| `thresholds.lookahead_hours` | How many hours ahead to evaluate (default: 24) |
| `watering_days` | Days your sprinkler runs |
| `watering_hour` | Hour your sprinkler starts (24h) |
| `sprinkler_pi.base_url` | URL of your sprinklers_pi web interface (e.g. `http://192.168.1.120:8080`) |

## Running

```bash
python rain_delay.py --config config.local.yaml

# Check current sprinkler run status
python rain_delay.py --config config.local.yaml --status

# Test without changing sprinkler state
python rain_delay.py --config config.local.yaml --dry-run
```

## Cron setup

Run the evening before each watering day (Mon, Wed, Sat for a Tue/Thu/Sun schedule):

```cron
0 21 * * 1,3,6 /path/to/venv/bin/python /home/craigh/sprinkler-rain-delay/rain_delay.py --config /home/craigh/sprinkler-rain-delay/config.local.yaml
```

## Sprinkler Pi integration

Communicates directly with [sprinklers_pi](https://github.com/rszimm/sprinklers_pi) via its web API:

- `GET /json/state` — reads current run status
- `GET /bin/run?system=off` — disables the "Run Schedules" switch
- `GET /bin/run?system=on` — re-enables it

No changes needed to the sprinklers_pi installation.

## Roadmap

- [ ] v1: Skip watering if rain is forecast (this)
- [ ] v2: If a day was skipped and rain didn't materialize, water the next eligible day
