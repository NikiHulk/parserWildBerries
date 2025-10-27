FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Playwright + Chromium ---
RUN pip install --no-cache-dir playwright && \
    playwright install-deps chromium && \
    playwright install chromium

COPY . .

CMD ["python", "-m", "bot.main"]
