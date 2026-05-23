# JARVIS — Docker

Containerises the **voice-agent worker** (the LLM/STT/TTS brain) +
**LiveKit SFU**. Voice-client (mic/speaker) and the Tauri desktop UI
stay on the **host** — they need direct audio devices + an X display,
which are clumsy to pass through.

## What gets containerised, what doesn't

| Component | Container? | Why |
|---|---|---|
| `voice-agent` worker | ✅ | Pure IO/CPU, no host devices needed |
| `livekit-server` (SFU) | ✅ | Network-only service |
| `voice-client` (`jarvis_voice_client.py`) | ❌ host | Needs `/dev/snd` for mic + speaker |
| Tauri desktop UI | ❌ host | Needs X display + tray |
| Chrome extension | ❌ host | Browser-resident |
| `computer_use` tool | ❌ in this image | Needs X display passthrough (see *Enabling computer_use* below) |
| `browser_task` headless | ⚠️ partial | Works headlessly; needs `~/.jarvis/browser-use-venv` mounted or rebuilt |

## Quick start

```bash
# 1. From the repo root, write your host UID/GID into a compose .env
#    so bind-mounted ~/.jarvis stays editable on the host.
echo "JARVIS_UID=$(id -u)"  > .env
echo "JARVIS_GID=$(id -g)" >> .env

# 2. Build + start
docker compose up -d

# 3. Tail the voice-agent logs
docker compose logs -f voice-agent
```

On first boot the entrypoint seeds `~/.jarvis/keys.env` from the
`keys.env.example` template. Edit it, then restart:

```bash
$EDITOR ~/.jarvis/keys.env
docker compose restart voice-agent
```

## Connecting the host voice-client to the containerised agent

The host's `voice-client` connects to `ws://127.0.0.1:7880` (LiveKit) —
same address whether LiveKit runs on the host or in the container,
because the compose file uses `network_mode: host`. Just run the host
voice-client as usual:

```bash
~/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python \
    ~/Documents/Projects/jarvis/src/voice-agent/jarvis_voice_client.py
```

If you previously ran `jarvis-voice-agent.service` via systemd, **stop
it first** — the container + the systemd unit can't both bind to the
same LiveKit room cleanly.

```bash
systemctl --user stop jarvis-voice-agent.service
```

## Updating

```bash
git pull
docker compose build voice-agent
docker compose up -d
```

The Python deps layer is cached on `requirements.txt`; source-only
changes rebuild in seconds.

## Enabling `computer_use` (X display passthrough)

`computer_use` drives the host's visible X11 desktop. To enable from
inside the container you need to mount the X socket and grant the
container access. **This weakens isolation** — only do it if you
understand the security trade-off.

```yaml
# Append to the voice-agent service in docker-compose.yml:
    volumes:
      - /tmp/.X11-unix:/tmp/.X11-unix:rw
      - ${XAUTHORITY:-~/.Xauthority}:/home/jarvis/.Xauthority:ro
    environment:
      - DISPLAY=${DISPLAY:-:0}
      - XAUTHORITY=/home/jarvis/.Xauthority
```

…then on the host, run `xhost +local:` once per session (or use a more
targeted ACL). On Wayland sessions you'll need additional plumbing
(XWayland or PipeWire screen capture); not covered here.

## Enabling `browser_task` (headless Chromium)

`browser_task` invokes the isolated `browser_use` venv at
`~/.jarvis/browser-use-venv`. Two options:

1. **Bind-mount the host's venv** (fast, requires the host already
   set it up via `install.sh`):
   ```yaml
   # voice-agent service:
       volumes:
         - ~/.jarvis/browser-use-venv:/opt/jarvis-data/browser-use-venv:ro
   ```

2. **Bake it into the image** — add to `Dockerfile` before the
   `USER jarvis` line:
   ```dockerfile
   RUN uv venv /opt/jarvis-data/browser-use-venv --python 3.13 \
       && uv pip install --python /opt/jarvis-data/browser-use-venv/bin/python \
              browser-use playwright \
       && /opt/jarvis-data/browser-use-venv/bin/playwright install --with-deps chromium
   ```
   …adds ~200 MB to the image. Skip if you don't need `browser_task`.

## Production tweaks

The defaults are for a dev box. Before exposing this on a real host:

- Replace LiveKit `--dev` mode with real keys
  (`setup/livekit/livekit-server.yaml`) and remove the `127.0.0.1`
  bind from the `livekit` service.
- Switch from `network_mode: host` to a bridge network with explicit
  port mappings so you control what's exposed.
- Configure a real reverse proxy (Caddy / Nginx) with TLS in front of
  the LiveKit port.
- Mount `keys.env` from a secrets manager (Docker secrets, sealed
  secrets, Vault) instead of bind-mounting from `~/.jarvis`.

## Troubleshooting

- **`livekit` health check fails**: the dev-mode SFU binds to `127.0.0.1`
  only; the `curl` healthcheck runs from inside the container's network
  namespace — with `network_mode: host` that's the same as the host.
  If it persistently fails, run `docker logs jarvis-livekit` to see the
  SFU startup output.
- **voice-agent can't see `keys.env`**: the entrypoint sources it from
  `/opt/jarvis-data/keys.env`. Confirm the bind mount worked:
  `docker compose exec voice-agent ls -la /opt/jarvis-data/`.
- **Build is slow**: the heavy Python deps layer is cached on
  `src/voice-agent/requirements.txt`. If you bump that file, expect a
  ~3-5 minute rebuild for the wheel installs. Subsequent code-only
  rebuilds finish in seconds.
- **Logs not in `~/.local/share/jarvis/logs/`**: confirm
  `~/.local/share/jarvis` exists on the host before `docker compose up`;
  Docker won't auto-create bind-mount sources.
