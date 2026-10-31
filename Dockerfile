# The image holds the bot process only. The Discord token is injected at
# runtime by Container Apps, never baked into a layer.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY cogs ./cogs

CMD ["python", "-u", "main.py"]
