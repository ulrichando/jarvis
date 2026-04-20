# Misty Scone — VM Validation Session (2026-04-19)

## Outcome

misty-core (Plans 2 + 4) **validated end-to-end inside a VMware VM**. Two real integration bugs surfaced during the dry-run, both fixed on branch `plan-5-hud-widget`. Plans 3 + 5 stay unit-test-only — VMware's SVGA adapter can't give Hyprland a working EGL surface, which blocks live validation of hyprland/screen tools and the HUD widget.

## Session artifacts on disk

- **VM**: `~/vmware/misty-base/misty-base.vmx` — Arch + misty-core, bridged to NAT `vmnet8` (192.168.77.0/24).
- **VM snapshot**: `base-misty-working` — daemon running with Plans 2 + 4 code, ready for curl. Restore: `vmrun -T ws revertToSnapshot ~/vmware/misty-base/misty-base.vmx base-misty-working`.
- **VM IP (NAT lease)**: `192.168.77.130`. May change across VMware reboots; check with `ssh ulrich@192.168.77.130 ip -4 addr`.
- **SSH**: `ssh -i ~/.ssh/id_ed25519 ulrich@192.168.77.130`. Public key was installed into `~ulrich/.ssh/authorized_keys` in the VM.
- **Arch ISO**: `~/vmware/iso/archlinux.iso` (Arch 2026.04.01).

## What runs in the VM

- Arch Linux 6.19.11-arch1-1, minimal install, GRUB on BIOS, ext4, no swap.
- User `ulrich`, hostname `ultron`.
- Bun 1.3.11 (from Arch `extra/bun`).
- misty-core at `~/jarvis/src/os/desktop/`, serving on `0.0.0.0:8765` (bound to all interfaces so the host reaches it via NAT).
- `.env` contains real `GROQ_API_KEY` and `DEEPSEEK_API_KEY` — rotate when done.

## What was validated live

| Endpoint / path | Status | Notes |
|---|---|---|
| `GET /health` | ✅ | Returns `{status:"ok"}`. |
| `GET /api/models` | ✅ | Returns provider + model. |
| `POST /api/think` (low-risk bash) | ✅ | Groq → tool_use bash → runs `echo ...` → tool_result → final text. |
| `POST /api/think` (high-risk bash) | ✅ | `sudo rm -rf ...` blocked with informative reason; `blocked[]` populated. |
| `POST /api/speak` | ✅ | Groq Orpheus, voice `daniel`, 96 KB WAV, PCM 16-bit mono 24 kHz. |
| `POST /api/transcribe` | ✅ | Groq Whisper round-trip; "hello from misty" → "Hello from Misty". |
| `POST /api/confirmation/:id` | Unit-only | Not exercised live; would need a client driving `?interactive=1`. |
| `hyprland` tool | ❌ | Needs Hyprland running with `$HYPRLAND_INSTANCE_SIGNATURE`. Hyprland crashed on VMware SVGA (EGL init failed). |
| `screen` tool | ❌ | Needs `grim` + Wayland compositor. Same blocker. |
| HUD widget (Plan 5) | ❌ | Needs eww + compositor. eww is AUR-only on Arch. |

## Bugs found + fixed

**1. Groq client used non-existent Anthropic-compatible endpoint** (`providers/groqClient.ts`)

The Plan 2 spec assumed Groq exposed an Anthropic-compatible `/anthropic/v1/messages` endpoint. It doesn't. A live `POST /api/think` returned `404 Unknown request URL: POST /anthropic/v1/messages`.

Fix: rewrote the client to use Groq's actual OpenAI-compatible endpoint at `/openai/v1/chat/completions`, with request/response translation between our internal Anthropic-shaped `ContentBlock` union and OpenAI's chat-completions/tool-call format at the provider boundary. Dropped `@anthropic-ai/sdk` from this client. 116-line rewrite; all 79 unit tests still pass.

Commit: `6dadd86` on `plan-5-hud-widget`.

**2. Hyprland tool let synchronous errors escape try/catch** (`agent/tools/hyprland.ts`)

`createHyprIpc()` calls `resolveSocketPath()` synchronously, which throws if `HYPRLAND_INSTANCE_SIGNATURE` is unset. In the old tool code, the `ipcFactory()` call was outside the try block, so the throw bubbled up to `/api/think` as an unwrapped 500 instead of being returned as a clean tool_result error.

Fix: moved `const ipc = ipcFactory()` inside the try block. Now if Hyprland isn't running, the agent loop gets `{output: "...HYPRLAND_INSTANCE_SIGNATURE not set...", is_error: true}` and the model can reason about it.

Commit: `b8cadc7` on `plan-5-hud-widget`.

## Why Hyprland-in-VMware doesn't work (for future-me)

Hyprland 0.54 uses **aquamarine** as its backend. Aquamarine needs a working DRM + EGL surface on `/dev/dri/card0`. VMware's SVGA II adapter (`/dev/dri/card0` in the guest) advertises GL capabilities but `eglInitialize` fails with `EGL_NOT_INITIALIZED (0x12289): DRI2: failed to create screen`. Even with `mks.enable3d = "TRUE"` in the VMX (exposing GL 4.3), the DRI2 path is broken in Mesa's vmwgfx driver for what aquamarine wants.

`WLR_RENDERER=pixman` doesn't help — that's a wlroots env var, and Hyprland 0.54 isn't wlroots. Aquamarine's equivalent is `AQ_NO_ATOMIC` but that only helps with DRM atomic-commit issues, not EGL init.

Paths that *might* work but weren't attempted:
- KVM/QEMU with VirtIO-GPU + virgl (modern Wayland-friendly virtualized GPU)
- VMware with a proper 3D-accelerated GPU from the guest's perspective (hasn't been a thing with plain VMware Workstation)
- Direct GPU passthrough (VFIO) — overkill for this use case

For now, accept that Plans 3 + 5 can't be live-tested in VMware. When misty-core is deployed on a real Linux+Hyprland machine eventually, that's where those tools get live-tested.

## Security note

Real API keys were pasted into the conversation transcript during this session. Anyone with access to the transcript sees them. **Rotate at:**
- Groq: https://console.groq.com/keys
- DeepSeek: https://platform.deepseek.com/api_keys

After rotating, update `~/jarvis/src/os/desktop/.env` in the VM (or equivalent deployment).

## Quick-start: pick up where we left off

1. Start the VM: `vmrun -T ws start ~/vmware/misty-base/misty-base.vmx nogui`
2. (If the snapshot was reverted or VM powered off cleanly) misty-core may not auto-restart. Restart it: `ssh -i ~/.ssh/id_ed25519 ulrich@192.168.77.130 'cd ~/jarvis/src/os/desktop && nohup bun run start >/tmp/misty.log 2>&1 </dev/null & disown'`
3. Verify: `curl http://192.168.77.130:8765/health`
4. Poke it: `curl -X POST http://192.168.77.130:8765/api/think -H 'content-type: application/json' -d '{"messages":[{"role":"user","content":"run `uname -a` via bash"}]}'`

To revert to the known-good snapshot if things break:
```
vmrun -T ws stop ~/vmware/misty-base/misty-base.vmx hard
vmrun -T ws revertToSnapshot ~/vmware/misty-base/misty-base.vmx base-misty-working
vmrun -T ws start ~/vmware/misty-base/misty-base.vmx nogui
```

## Next sensible plans

- **Deploy misty-core to a real Linux+Hyprland machine** (not VMware) so Plans 3 + 5 can be live-validated.
- **Plan 6 (audio client)** — a host/desktop client that consumes `/api/speak` + `/api/transcribe` + `/api/confirmation`. Doesn't need the VM to validate; can be built and tested against the running VM misty-core.
- **Merge `plan-5-hud-widget` bugfixes back to earlier branches** if those branches will ever be used standalone (probably won't — future work branches from the latest).
