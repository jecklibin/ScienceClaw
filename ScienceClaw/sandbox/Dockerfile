# syntax=docker/dockerfile:1.4
#FROM ghcr.io/agent-infra/sandbox:latest
FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest


# ── 0. 统一 Python 环境为 3.12 ─────────────────────────────────────
ENV PATH="/opt/python3.12/bin:${PATH}"
RUN ln -sf /opt/python3.12/bin/python3.12 /usr/local/bin/python3 \
    && ln -sf /opt/python3.12/bin/python3.12 /usr/local/bin/python \
    && ln -sf /opt/python3.12/bin/pip3 /usr/local/bin/pip3 \
    && ln -sf /opt/python3.12/bin/pip3 /usr/local/bin/pip

# ── 1. 复制依赖文件 ──────────────────────────────
COPY requirements.txt /tmp/requirements.txt
# ── 2. 一次性安装所有依赖（系统 + Node + Python，BuildKit 缓存加速）──────────────────────
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential clang \
        fonts-wqy-microhei fontconfig \
        poppler-utils pandoc libreoffice-writer \
        xvfb x11vnc websockify fluxbox \
    && fc-cache -fv
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm config set registry ${NPM_MIRROR} \
    && npm install -g skills docx pptxgenjs
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip config set global.timeout 600 \
    && python3 -m pip install -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt \
    && python3 -c "import matplotlib; matplotlib.get_cachedir()" \
    && python3 -c "import matplotlib.font_manager; matplotlib.font_manager._load_fontmanager(try_read_cache=False)"

ENV CC=clang NODE_PATH="/usr/lib/node_modules"
RUN mkdir -p /home/scienceclaw && chmod -R 777 /home/scienceclaw
# ── 3. 项目文件 ────────────────────────────────────────────────────────
COPY tool_runner.py /app/_tool_runner.py
