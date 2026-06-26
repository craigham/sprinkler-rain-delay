#!/bin/sh
set -e

# Read bot token from Docker secret and export for bot.py
if [ -f /run/secrets/bot_token ]; then
    export DISCORD_BOT_TOKEN=$(cat /run/secrets/bot_token)
fi

# Start cron as a daemon, then run the Discord bot in the foreground
cron
exec python /app/bot.py
