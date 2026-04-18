package com.jarvis.android.domain.model

// ── File item ─────────────────────────────────────────────────────────────────

data class FileItem(
    val path:         String,
    val name:         String,
    val isDirectory:  Boolean,
    val isSymlink:    Boolean,
    val sizeBytes:    Long,
    val permissions:  String,    // e.g. "rwxr-xr-x"
    val owner:        String,
    val group:        String,
    val lastModified: Long,
) {
    val extension: String get() = if (isDirectory) "" else name.substringAfterLast('.', "")
    val isHidden:  Boolean get() = name.startsWith('.')
}

// ── File stats ────────────────────────────────────────────────────────────────

data class FileStats(
    val path:         String,
    val sizeBytes:    Long,
    val isDirectory:  Boolean,
    val isReadable:   Boolean,
    val isWritable:   Boolean,
    val isExecutable: Boolean,
    val lastModified: Long,
    val mimeType:     String?,
)
