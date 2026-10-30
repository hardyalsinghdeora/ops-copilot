FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY data/knowledge_base ./data/knowledge_base
COPY data/fixtures ./data/fixtures

RUN pip install --no-cache-dir -e . \
 && ops-copilot ingest   # bakes the local ONNX index into the image

EXPOSE 8000
CMD ["ops-copilot", "serve"]
