package com.jarvis.android.domain.model

// ── System info snapshot ──────────────────────────────────────────────────────

data class SystemInfo(
    val deviceModel:    String,
    val androidVersion: String,
    val sdkInt:         Int,
    val arch:           String,
    val ramTotalMb:     Long,
    val ramAvailMb:     Long,
    val ramLowMemory:   Boolean,
    val batteryPct:     Int,
    val batteryCharging:Boolean,
    val uptimeMs:       Long,
    val kernelVersion:  String,
    val isRooted:       Boolean,
)

// ── Process ───────────────────────────────────────────────────────────────────

data class ProcessInfo(
    val pid:         Int,
    val ppid:        Int,
    val user:        String,
    val rssKb:       Long,
    val cpuPercent:  Float,
    val name:        String,
)

// ── Installed app ─────────────────────────────────────────────────────────────

data class AppInfo(
    val packageName: String,
    val label:       String,
    val isSystem:    Boolean,
    val versionName: String?,
    val targetSdk:   Int,
)
