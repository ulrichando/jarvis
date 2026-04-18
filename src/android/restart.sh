#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "▶ Building..."
./gradlew assembleDebug -q
echo "▶ Installing..."
adb install -r app/build/outputs/apk/debug/app-debug.apk
echo "▶ Restarting..."
adb shell am force-stop com.jarvis.android.debug
adb shell am start -n com.jarvis.android.debug/com.jarvis.android.MainActivity
echo "✓ Done"
