FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    procps curl ca-certificates jq git unzip ncurses-term\
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 SHELL=/bin/bash

# Zellij
ARG ZELLIJ_VERSION=0.41.2
RUN curl -fsSL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin \
    && chmod +x /usr/local/bin/zellij

# yazi（ファイルツリー）
ARG YAZI_VERSION=0.4.2
RUN curl -fsSL "https://github.com/sxyazi/yazi/releases/download/v${YAZI_VERSION}/yazi-x86_64-unknown-linux-musl.zip" -o /tmp/yazi.zip \
    && unzip -qo /tmp/yazi.zip "yazi-x86_64-unknown-linux-musl/yazi" -d /tmp/yazi_ex \
    && mv /tmp/yazi_ex/yazi-x86_64-unknown-linux-musl/yazi /usr/local/bin/yazi \
    && chmod +x /usr/local/bin/yazi \
    && rm -rf /tmp/yazi.zip /tmp/yazi_ex

# uv（mcp-server-fetch / mcp-server-git 用）
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Antigravity CLI (agy)
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash \
    && mv /root/.local/bin/agy /usr/local/bin/agy

# zjstatus plugin（zellij フッター用）
ARG ZJSTATUS_VERSION=0.19.1
RUN mkdir -p /home/node/.config/zellij/plugins \
    && curl -fsSL "https://github.com/dj95/zjstatus/releases/download/v${ZJSTATUS_VERSION}/zjstatus.wasm" \
    -o /home/node/.config/zellij/plugins/zjstatus.wasm

# zellij 設定・レイアウト
COPY .devcontainer/zellij-config.kdl /home/node/.config/zellij/config.kdl
COPY .devcontainer/zellij-layout.kdl /home/node/.config/zellij/layouts/claude.kdl

# yazi テーマ
COPY .devcontainer/yazi-theme.toml /home/node/.config/yazi/theme.toml

# zjstatus スクリプト
COPY .devcontainer/scripts/ /home/node/.config/zellij/scripts/
RUN chmod +x /home/node/.config/zellij/scripts/*.sh

# Claude hooks（トークン追跡）
RUN mkdir -p /home/node/.claude/hooks
COPY .devcontainer/hooks/update-stats.sh /home/node/.claude/hooks/update-stats.sh
RUN chmod +x /home/node/.claude/hooks/update-stats.sh \
    && printf '{\n  "hooks": {\n    "Stop": [{\n      "matcher": "",\n      "hooks": [{"type": "command", "command": "/home/node/.claude/hooks/update-stats.sh"}]\n    }]\n  }\n}\n' \
    > /home/node/.claude/settings.json

# コンテナアタッチ時に zellij を自動起動
RUN printf '\n# Auto-start zellij on interactive shell attach\nif [ -z "$ZELLIJ" ] && [ -n "$PS1" ]; then\n  exec zellij\nfi\n' \
    >> /home/node/.bashrc

# 所有権を node ユーザーに修正
RUN chown -R node:node /home/node

WORKDIR /workspace
USER node
