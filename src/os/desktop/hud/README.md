# misty HUD

Eww / GTK layer-shell widget that shows misty-core's health + pending confirmations, with Accept/Deny buttons that POST to `/api/confirmation/:id`.

## Prerequisites (VM)

- Hyprland (Omarchy includes this).
- `eww`, `jq`, `curl` (installed by `install.sh` via pacman).
- misty-core running on `http://127.0.0.1:8765` (the default).

## Install

```bash
cd ~/jarvis/src/os/desktop/hud
./install.sh
```

This installs `eww`/`jq`/`curl` and symlinks this directory into `~/.config/eww/misty`.

## Start

```bash
eww -c ~/.config/eww/misty daemon
eww -c ~/.config/eww/misty open misty-hud
```

The HUD anchors to the top-right corner. It polls misty-core every 1s. On restart of misty-core, the HUD will automatically flip from `down` → `healthy`.

Stop:

```bash
eww -c ~/.config/eww/misty close misty-hud
eww -c ~/.config/eww/misty kill
```

## Autostart under Hyprland

Append to `~/.config/hypr/hyprland.conf`:

```
exec-once = eww -c ~/.config/eww/misty daemon
exec-once = eww -c ~/.config/eww/misty open misty-hud
```

## How approval flows

1. A user sends `POST /api/think?interactive=1 {"messages":[...]}` requesting a high-risk action (e.g., `sudo pacman -Syu`).
2. misty-core's agent loop hits the gate, which classifies the request as high-risk and opens a confirmation (id `c_abc123`).
3. The HUD, polling every 1s, discovers the new pending entry and renders it with Accept/Deny buttons.
4. User clicks Accept → `confirm.sh c_abc123 allow` → `POST /api/confirmation/c_abc123 {"decision":"allow"}`.
5. misty-core's gate resolves, the agent loop continues, the original `/api/think` response returns with the tool executed.

## Troubleshooting

- **HUD shows `down`:** misty-core isn't running on `127.0.0.1:8765`. Start it: `cd ~/jarvis/src/os/desktop && bun run start`.
- **No widget appears:** `eww log` (tail) — most commonly a config path issue or a GTK layer-shell compositor (Hyprland works; X11 doesn't).
- **Custom port:** set `MISTY_URL=http://127.0.0.1:8866` in your shell env before `./install.sh` and `eww daemon` — the scripts honour this.
