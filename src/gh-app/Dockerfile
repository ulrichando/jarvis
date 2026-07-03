# src/gh-app/Dockerfile — ONE image, two roles:
#   service:  CMD bun src/gh-app/server.ts          (webhook + worker)
#   sandbox:  cmd bun src/gh-app/containerEntry.ts  (spawned per job by the worker)
#
# Build context is the REPO ROOT (compose: context ../.., dockerfile
# src/gh-app/Dockerfile) because the sandbox role runs the full CLI:
# executeTask resolves repo-root bin/jarvis relative to src/cli/src/gh-agent,
# so the image needs bin/ + src/cli/ + src/gh-app/ in the repo shape.
#
# bin/jarvis hardcodes the dev checkout root, so the repo is installed AT that
# path; /app is a symlink to it for ergonomic docker exec / WorkingDir use.
FROM oven/bun:1.3

# git for the engine's clone/commit/push; gh for the API/PR calls
# (executeTask drives both). gh comes from GitHub's official apt repo.
RUN apt-get update \
  && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
  && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update && apt-get install -y --no-install-recommends gh \
  && rm -rf /var/lib/apt/lists/*

# Repo lives at the path bin/jarvis expects (see header note).
ENV REPO_DIR=/home/ulrich/Documents/Projects/jarvis
WORKDIR ${REPO_DIR}

# Engine + CLI first (bigger, changes less than gh-app) for layer caching.
COPY bin ./bin
COPY src/cli ./src/cli
RUN cd src/cli && bun install --frozen-lockfile --production

COPY src/gh-app ./src/gh-app

RUN ln -s ${REPO_DIR} /app
WORKDIR /app

# Creds fallback dir for the service role (volume-mounted in compose).
RUN mkdir -p /data

EXPOSE 8790
CMD ["bun", "src/gh-app/server.ts"]
