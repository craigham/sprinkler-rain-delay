#!/bin/sh
set -e

# Inject WU API key from Docker secret into config before cron starts
SECRET_FILE="/run/secrets/wu_api_key"
if [ -f "$SECRET_FILE" ]; then
    API_KEY=$(cat "$SECRET_FILE")
    sed -i "s|wu_api_key:.*|wu_api_key: \"${API_KEY}\"|" /app/config.yaml
fi

exec cron -f
