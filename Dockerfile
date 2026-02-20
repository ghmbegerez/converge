FROM python:3.12-slim AS base

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy source
COPY src/ src/
RUN pip install --no-cache-dir -e .

EXPOSE 9876

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9876/health/live')"

ENTRYPOINT ["uvicorn", "converge.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "9876"]
