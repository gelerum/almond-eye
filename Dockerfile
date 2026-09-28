# Almond Eye + Claude Code в одном контейнере.
FROM python:3.12-slim-bookworm

ARG USER_UID=1000
ARG USER_GID=1000
ARG INSTALL_DETECTOR=1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120 \
    UV_HTTP_RETRIES=10 \
    VIRTUAL_ENV=/opt/venv \
    CLAUDE_CONFIG_DIR=/home/dev/.claude \
    PATH=/opt/venv/bin:/home/dev/.local/bin:$PATH

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/

# Системные пакеты: git/ripgrep для Claude Code, node для веб-тестов, libgl/glib для OpenCV (ultralytics).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git ripgrep less procps sudo nodejs npm \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g ${USER_GID} dev \
    && useradd -m -u ${USER_UID} -g ${USER_GID} -s /bin/bash dev \
    && echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev \
    && mkdir -p /opt/venv /workspace && chown dev:dev /opt/venv /workspace

USER dev

# Python-зависимости из uv.lock (extra experiment = ultralytics + transformers для ансамбля) в отдельном venv, чтобы не пересекаться с .venv хоста.
COPY --chown=dev:dev pyproject.toml uv.lock /tmp/proj/
RUN cd /tmp/proj \
    && uv sync --frozen --no-install-project --no-cache $([ "$INSTALL_DETECTOR" = "1" ] && echo --extra experiment)

# Claude Code (нативный установщик ставит в ~/.local/bin).
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && mkdir -p /home/dev/.claude

WORKDIR /workspace
COPY --chown=dev:dev . /workspace
COPY --chown=dev:dev docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
