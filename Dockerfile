FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    TZ=Asia/Shanghai

RUN groupadd -g 1000 developer \
    && useradd -m -u 1000 -g 1000 -s /bin/bash developer \
    && apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --chown=developer:developer requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=developer:developer . .

RUN chown -R developer:developer /app

USER developer

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f "python main.py" || exit 1

CMD ["python", "main.py"]
