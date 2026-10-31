FROM python:3.12-slim

WORKDIR /app
# OPS_COPILOT_ROOT: the package is installed into site-packages, the data lives here
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPS_COPILOT_ROOT=/app

COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir -c requirements.lock .

COPY data/knowledge_base ./data/knowledge_base
COPY data/fixtures ./data/fixtures
RUN useradd --create-home --uid 1000 app \
 && mkdir -p data/.drafts \
 && chown -R app:app data
USER app

# bakes the index, and the local ONNX embedding model (cached under ~app), into the image
RUN ops-copilot ingest

EXPOSE 8000
# same port the server picks (OPS_COPILOT_API_PORT, else PORT as set by hosts like Render, else 8000)
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; from ops_copilot.config import get_settings; urllib.request.urlopen(f'http://127.0.0.1:{get_settings().api_port}/health', timeout=4)"]
CMD ["ops-copilot", "serve"]
