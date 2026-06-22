# Forge API - FastAPI on the MIT LangChain/LangGraph stack.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Build deps for psycopg/cryptography wheels (kept minimal; most ship manylinux wheels).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching). Copy only metadata, then the package.
COPY pyproject.toml README.md ./
COPY forge ./forge
# Production extras: model providers, vectors, knowledge, MCP, workers (Redis/arq), Postgres.
RUN pip install --upgrade pip && \
    pip install -e ".[providers,vectors,knowledge,mcp,workers,postgres]"

COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

# Run as a non-root user.
RUN useradd --create-home --uid 10001 forge && chown -R forge:forge /app
USER forge

EXPOSE 8000

# Container healthcheck hits the readiness probe (DB + checkpointer).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/readyz || exit 1

# Apply migrations, then serve. (Schema also self-bootstraps via create_all, but on managed
# Postgres `alembic upgrade head` is the controlled path.) Honor --proxy-headers behind an LB.
CMD ["sh", "-c", "alembic upgrade head && uvicorn forge.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
