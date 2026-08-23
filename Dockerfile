# syntax=docker/dockerfile:1
#
# Two stages so the demo image stays small: the ML extras (numpy, pandas,
# scikit-learn, pyarrow) are ~200MB and only the frontier needs them.
#
#   docker build -t amanat .                           # web demo (default, Cloud Run)
#   docker build --target demo -t amanat:demo .        # governance demo + tests
#   docker build --target ml   -t amanat:ml   .        # + ceiling frontier

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Dependency layer first so source edits do not invalidate the install cache.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# ---------------------------------------------------------------- demo
FROM base AS demo
RUN pip install --no-cache-dir -e ".[dev]"
COPY tests/ ./tests/
# Fails the build if the containment suite does not pass — an image that cannot
# demonstrate its own guarantees should not be publishable.
RUN pytest tests/ -q --ignore=tests/test_ceiling.py
CMD ["python", "-m", "amanat.demo"]

# ---------------------------------------------------------------- ml
FROM base AS ml
RUN pip install --no-cache-dir -e ".[ml,dev]"
COPY tests/ ./tests/
# NYC TLC parquet files land here; mount a volume to avoid re-downloading ~100MB
# on every run.
VOLUME ["/app/data"]
CMD ["python", "-m", "amanat.ceiling.frontier"]


# ---------------------------------------------------------------- web (default)
# The interactive governed-core demo. Credential-free, so it is safe to expose:
# no LLM, no real payment rail, nothing that can move money or spend a quota.
# Listens on $PORT for Cloud Run (defaults to 8080 locally).
FROM base AS web
RUN pip install --no-cache-dir -e ".[web]"
COPY web/ ./web/
ENV PYTHONPATH=/app/src:/app
EXPOSE 8080
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
