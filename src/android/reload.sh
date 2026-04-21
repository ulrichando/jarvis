#!/usr/bin/env bash
# Android app hot-reload: rebuild + reinstall + relaunch — preserving
# granted permissions and any login/auth state across reloads.
#
# Usage: ./reload.sh [device]
#   device defaults to the first `adb devices` entry.
#
# What this does NOT do (deliberately):
#   - `pm clear` the app. That wipes permissions + data; we want the mic
#     grant, any stored server settings, chat history, etc. to persist.
#     WebView HTML assets are bundled in the APK so `install -r` already
#     gets us fresh HTML.

set -euo pipefail
cd "$(dirname "$0")"

PKG=com.jarvis.android.debug
DEVICE="${1:-$(adb devices | awk 'NR==2 {print $1}')}"
if [ -z "$DEVICE" ]; then
  echo "no device found — plug in or start an emulator"
  exit 1
fi
export ANDROID_SERIAL="$DEVICE"

# JDK 25 is -ea and breaks Kotlin — pin to 21.
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export PATH="$JAVA_HOME/bin:$PATH"

echo "▸ rebuild debug APK"
./gradlew :app:assembleDebug -q

APK=app/build/outputs/apk/debug/app-debug.apk
echo "▸ force-stop (kills WebView process; new one reads fresh assets)"
adb shell am force-stop "$PKG" || true

echo "▸ install $APK (install -r preserves data + permissions)"
# USB cable on this host is flaky — streamed install sometimes drops.
# Fall back to push-then-pm-install, which only needs one short write
# per step instead of a sustained streaming connection.
if ! adb install -r "$APK" 2>&1 | grep -q Success; then
  echo "  streamed install dropped — retry via push + pm install"
  for try in 1 2 3 4 5; do
    sleep 2
    adb devices | grep -q "$DEVICE.*device$" || continue
    adb push "$APK" /data/local/tmp/jarvis.apk >/dev/null 2>&1 || continue
    sleep 1
    if adb shell "pm install -r /data/local/tmp/jarvis.apk" 2>&1 | grep -q Success; then
      adb shell rm /data/local/tmp/jarvis.apk 2>/dev/null
      break
    fi
  done
fi

# First-time grants for a fresh install. If the app is already installed
# with permissions granted, these are no-ops (idempotent).
if ! adb shell dumpsys package "$PKG" 2>/dev/null | grep -q "RECORD_AUDIO: granted=true"; then
  echo "▸ first-time grant of essential runtime permissions"
  for perm in \
      android.permission.RECORD_AUDIO \
      android.permission.POST_NOTIFICATIONS \
      android.permission.ACCESS_FINE_LOCATION \
      android.permission.ACCESS_COARSE_LOCATION \
      android.permission.READ_EXTERNAL_STORAGE; do
    adb shell pm grant "$PKG" "$perm" >/dev/null 2>&1 || true
  done
else
  echo "▸ permissions already granted — keeping them"
fi

echo "▸ launch"
adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1

echo "done"
