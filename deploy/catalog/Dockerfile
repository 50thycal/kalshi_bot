FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'httpx==0.28.1' 'psycopg[binary]==3.3.6'
COPY kalshi_bot/catalog ./kalshi_bot/catalog
COPY kalshi_bot/mmsell/market_types.py ./kalshi_bot/mmsell/market_types.py
COPY kalshi_bot/registry/series_manifest.json ./kalshi_bot/registry/series_manifest.json
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "kalshi_bot.catalog.service"]
