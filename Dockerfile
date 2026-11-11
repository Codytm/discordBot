# The image holds the bot process only. The Discord token is injected at
# runtime by Container Apps, never baked into a layer.
FROM python:3.12-slim

# ffmpeg turns a YouTube audio stream into PCM for Discord voice.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY cogs ./cogs

CMD ["python", "-u", "main.py"]
