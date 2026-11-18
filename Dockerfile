FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Non-root runtime user, plus the data directory the app writes into.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data

# Install dependencies in their own cached layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application sources. .dockerignore keeps .env, .venv, tests and
# local caches out of the image, so new modules are picked up automatically.
COPY . .

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8501

# python:3.12-slim has no curl; the stdlib HTTP client keeps the image lean.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--server.enableCORS=false"]
