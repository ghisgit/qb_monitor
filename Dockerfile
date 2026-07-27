FROM python:3.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

RUN groupadd -g 1000 developer \
    && useradd -m -u 1000 -g 1000 -s /bin/bash developer \
    && apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY --chown=developer:developer . .

RUN chown -R developer:developer /app

USER developer

ENV PATH="/app/.venv/bin:$PATH"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f "python main.py" || exit 1

CMD ["python", "main.py"]
