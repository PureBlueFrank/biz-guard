FROM python:3.12.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIZGUARD_TRANSPORT=streamable-http \
    BIZGUARD_HOST=0.0.0.0 \
    BIZGUARD_PORT=8000 \
    BIZGUARD_REPOSITORY_ROOT=/workspace/repos \
    BIZGUARD_APPROVAL_DB=/var/lib/bizguard/approvals.sqlite3 \
    BIZGUARD_CONTEXT_DB=/var/lib/bizguard/contexts.sqlite3

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir --constraint requirements-production.lock . \
    && addgroup --system bizguard \
    && adduser --system --ingroup bizguard --no-create-home bizguard \
    && mkdir -p /workspace/repos /var/lib/bizguard \
    && chown -R bizguard:bizguard /var/lib/bizguard

USER bizguard
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
ENTRYPOINT ["python", "agents_mcp/server.py"]
