# jarvis-workbench: per-workspace sandbox.
#
# One image, one container per workspace. The workspace dir is bind-mounted
# at /workspace; the container itself is a passive `sleep infinity` that we
# `docker exec` into for terminal sessions. Files created by the container
# end up on the host fs, so the existing REST API and JARVIS CLI keep
# working unchanged.

FROM node:20-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl wget ca-certificates build-essential python3 python3-pip \
      ripgrep jq nano vim-tiny procps less openssh-client unzip \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (gh) — lets a /code session open pull requests (`gh pr create`).
# In container sessions it is pre-authenticated via the GH_TOKEN env the web
# app passes to the child, so no `gh auth login` is needed.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Bun (faster JS runtime, used by lots of bolt-style projects)
RUN curl -fsSL https://bun.sh/install | bash \
    && mv /root/.bun /usr/local/bun \
    && ln -s /usr/local/bun/bin/bun /usr/local/bin/bun \
    && ln -s /usr/local/bun/bin/bunx /usr/local/bin/bunx

# pnpm (yarn is already provided by node:20 via corepack shims)
RUN npm install -g pnpm@9 && corepack enable

# Friendly default prompt + ls colors when the user execs in
RUN echo 'export PS1="\\[\\e[36m\\]\\u@workbench\\[\\e[0m\\]:\\[\\e[33m\\]\\w\\[\\e[0m\\]$ "' >> /etc/bash.bashrc \
    && echo 'alias ls="ls --color=auto"' >> /etc/bash.bashrc \
    && echo 'alias ll="ls -lah --color=auto"' >> /etc/bash.bashrc

WORKDIR /workspace

# Single canonical dev-server port. The system prompt tells the AI to
# always bind to 5173 (vite/next/whatever), so we only need one mapping.
# Keeps the runtime UI uncluttered and avoids the "is this multiple
# sandboxes?" confusion of a row of 8 chips.
EXPOSE 5173

CMD ["sleep", "infinity"]
