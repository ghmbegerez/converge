# =============================================================================
# Multi-stage Dockerfile for Converge
# Targets: api (default), worker
# =============================================================================

FROM python:3.12-slim AS base

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir "psycopg[binary]>=3.1.0" "psycopg_pool>=3.2.0"

# Copy source
COPY src/ src/
COPY migrations/ migrations/
RUN pip install --no-cache-dir -e .

# =============================================================================
# API server target (default)
# =============================================================================
FROM base AS api

EXPOSE 9876

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9876/health/live')"

ENTRYPOINT ["uvicorn", "converge.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "9876"]

# =============================================================================
# Worker target
# =============================================================================
FROM base AS worker

# Worker has no HTTP port
ENTRYPOINT ["python", "-m", "converge.worker"]
