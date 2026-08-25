FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIZGUARD_TRANSPORT=streamable-http \
    BIZGUARD_HOST=0.0.0.0 \
    BIZGUARD_PORT=8000 \
    BIZGUARD_REPOSITORY_ROOT=/workspace/repos \
    BIZGUARD_EMBEDDING_CACHE_DIR=/var/lib/bizguard/embeddings

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir --constraint requirements-production.lock '.[production]' \
    && addgroup --system bizguard \
    && adduser --system --ingroup bizguard --no-create-home bizguard \
    && mkdir -p /workspace/repos /var/lib/bizguard \
    && chown -R bizguard:bizguard /var/lib/bizguard

USER bizguard
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)"]
ENTRYPOINT ["python", "agents_mcp/server.py"]
