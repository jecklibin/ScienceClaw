# syntax=docker/dockerfile:1.4
ARG BASE_IMAGE=python:3.13-slim
ARG INSTALL_CHROMIUM=true
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    RUNTIME_ADAPTER_WORKSPACE_ROOT=/workspace \
    RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads \
    RUNTIME_ADAPTER_VERSION=local-image \
    RUNTIME_ADAPTER_TOKEN= \
    RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH=false \
    RUNTIME_ADAPTER_BROWSER_EXECUTABLE= \
    RUNTIME_ADAPTER_BROWSER_DEBUG_PORT=9222 \
    RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_URL= \
    RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL=

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && if [ "${INSTALL_CHROMIUM}" = "true" ]; then \
      apt-get install -y --no-install-recommends chromium; \
    fi \
    && rm -rf /var/lib/apt/lists/*

COPY runtime-adapter/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY backend /app/backend

RUN mkdir -p /workspace \
    && python -m backend.runtime.adapter_app --self-check

EXPOSE 8080 9222

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -m backend.runtime.adapter_app --self-check || exit 1

CMD ["python", "-m", "uvicorn", "backend.runtime.adapter_app:app", "--host", "0.0.0.0", "--port", "8080"]
