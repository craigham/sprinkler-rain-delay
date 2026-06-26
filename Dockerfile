FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends cron && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rain_delay.py config.yaml ./

# Run at 2am on Tue, Thu, Sun — just before the 3am watering window
RUN echo "0 2 * * 2,4,0 root python /app/rain_delay.py >> /proc/1/fd/1 2>&1" > /etc/cron.d/rain-delay \
    && chmod 0644 /etc/cron.d/rain-delay \
    && crontab /etc/cron.d/rain-delay

CMD ["cron", "-f"]
