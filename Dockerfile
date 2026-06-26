FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rain_delay.py bot.py water_balance.py config.yaml entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Log file → container stdout so Docker/journald captures it
RUN ln -sf /proc/1/fd/1 /var/log/sprinkler-rain-delay.log

# 2am Tue, Thu, Sun — one hour before the 3am watering window
RUN echo "0 2 * * 2,4,0 root cd /app && python rain_delay.py --config /app/config.yaml >> /proc/1/fd/1 2>&1" \
        > /etc/cron.d/rain-delay \
    && chmod 0644 /etc/cron.d/rain-delay

ENTRYPOINT ["/app/entrypoint.sh"]
