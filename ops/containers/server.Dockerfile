# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.10.10 AS uv

FROM python:3.13.9-slim-bookworm AS builder
COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock alembic.ini ./
COPY apps ./apps
COPY packages ./packages
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.13.9-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    QUANTX_ROOT=/app \
    HOME=/var/lib/quantx \
    LOG_FILE=""
WORKDIR /app

RUN groupadd --system --gid 10001 quantx \
    && useradd --system --uid 10001 --gid quantx --home-dir /var/lib/quantx quantx \
    && mkdir -p /var/lib/quantx /tmp/quantx \
    && chown -R quantx:quantx /var/lib/quantx /tmp/quantx
COPY --from=builder --chown=quantx:quantx /app /app

USER 10001:10001
CMD ["python", "-m", "quantx_api.main"]
