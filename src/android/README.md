# JARVIS Android Client

Root-level personal AI command-and-control for Android.  
Claude AI (Anthropic) · PTY terminal · Full filesystem · System control · Root shell

---

## Requirements

| Requirement | Detail |
|---|---|
| **Root** | Magisk ≥ 27.0 or KernelSU ≥ 0.9.0 |
| **Android** | API 26+ (Android 8.0 Oreo) |
| **Arch** | arm64-v8a or x86_64 |
| **API key** | Anthropic API key (`sk-ant-api03-…`) |
| **Build** | Android Studio Ladybug / Gradle 8.9 / NDK 27.2 |

---

## Building

```bash
# Clone and open in Android Studio, or:
cd src/android

# Debug APK
./gradlew assembleDebug

# Run unit tests
./gradlew testDebugUnitTest

# Install on device (must have adb connected)
./gradlew installDebug
```

The first build compiles the NDK PTY bridge (`openpty.cpp`, `vt_parser.cpp`).
Subsequent builds are incremental.

---

## Architecture

```
presentation/          Compose screens + ViewModels (MVI)
  chat/                Claude AI chat with streaming + tool_use agent loop
  terminal/            PTY terminal emulator with session tabs
  filesystem/          File manager with root toggle + breadcrumbs
  system/              CPU / RAM / processes / apps / logcat
  network/             WiFi scan + ip route
  sensors/             Live sensor grid + GPS + orientation
  permissions/         Three-tier permission matrix
  settings/            API key + endpoint config
  onboarding/          First-launch setup flow

domain/
  model/               Pure Kotlin domain models (no Android)
  repository/          Repository interfaces
  usecase/             Single-responsibility use case wrappers

data/
  api/                 Claude SSE streaming, tool_use accumulator
  local/               Room database (conversations, messages, command history)
  repository/          Repository implementations

system/
  root/                libsu RootManager + RootShell + JarvisRootService
  terminal/            NDK PTY bridge + VT100 parser + TerminalSessionManager
  tools/               JarvisToolDispatcher (16 Claude tools → Android actions)
  permissions/         PermissionManager (Dangerous / Special / Root tiers)

service/               Android long-lived services
  JarvisForegroundService    Keeps root shell + PTY alive
  JarvisNotificationListener Reads all notifications
  JarvisAccessibilityService Observes any screen content
  JarvisDeviceAdmin          Lock / wipe / policy enforcement
  BootReceiver               Auto-start on reboot

di/                    Hilt modules (Network, Database, Repository, System)
navigation/            Compose NavGraph + Screen sealed class
```

---

## Claude Tool Set

The agent loop supports 16 tools dispatched by `JarvisToolDispatcher`:

| Tool | Description |
|---|---|
| `bash_exec` | Run shell command (root optional) |
| `read_file` | Read file content (root-aware) |
| `write_file` | Write/append to file |
| `list_directory` | List directory entries |
| `get_system_info` | CPU / RAM / battery / uptime snapshot |
| `list_processes` | Top N processes by CPU / RAM |
| `kill_process` | Send signal to PID |
| `list_installed_apps` | Enumerate packages |
| `get_logcat` | Recent logcat with tag filter |
| `network_scan` | WiFi SSIDs and signal levels |
| `get_sensors` | Device sensor snapshot |
| `terminal_create` | Open PTY session |
| `terminal_write` | Write text to PTY session |
| `terminal_kill` | Close PTY session |
| `set_clipboard` | Write to system clipboard |
| `get_clipboard` | Read clipboard text |

Destructive commands (`rm -rf`, `reboot`, `mkfs`, `dd of=/dev/…`, …) trigger a
confirmation dialog before execution.

---

## PTY Terminal

The terminal emulator uses a JNI bridge (`libpty_bridge.so`) that calls
POSIX `openpty()` from Bionic libc (unlocked via `-D_XOPEN_SOURCE=700`).

```
openpty() → fork() → setsid() → TIOCSCTTY → exec /system/bin/sh
```

The VT100/xterm-256 state machine is implemented in `vt_parser.cpp` and renders
into a 13-byte-per-cell grid consumed by the Compose `TerminalView` Canvas.

---

## Security Model

| Layer | Mechanism |
|---|---|
| API key | AES-256-GCM via EncryptedSharedPreferences + Android Keystore |
| Dangerous tools | Regex gate → confirmation dialog before execution |
| Root access | libsu: per-call grant, logged in Magisk/KernelSU grant log |
| Network | OkHttp; no plaintext allowed for `api.anthropic.com` |

---

## Running Tests

```bash
# All unit tests (JUnit 5 + MockK + Turbine)
./gradlew testDebugUnitTest

# Specific test class
./gradlew testDebugUnitTest --tests "com.jarvis.android.data.api.SseParserTest"

# Test report
open app/build/reports/tests/testDebugUnitTest/index.html
```

---

## First-Run Setup

1. Install and open the app.
2. Complete the onboarding flow:
   - Enter your Anthropic API key (stored encrypted on-device).
   - Grant permissions via the permission matrix.
3. Grant root in Magisk / KernelSU when prompted.
4. Open the Chat screen and start talking to JARVIS.

---

## Deep Link

```
jarvis://chat?session=<conversationId>
```

Opens the chat screen directly to the specified conversation. Handled by
`MainActivity` via `launchMode="singleTask"`.

---

## Dependency roadmap

AGP 8.7.3 → 9.x is a planned major upgrade (requires Gradle 9, new DSL interfaces; AGP 10 mid-2026 removes the 8.x opt-out). Deferred — verify LiteRT-LM / Kotlin 2.2 metadata coupling before bumping Kotlin past 2.2.
