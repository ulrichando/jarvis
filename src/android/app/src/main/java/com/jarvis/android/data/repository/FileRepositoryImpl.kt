package com.jarvis.android.data.repository

import android.webkit.MimeTypeMap
import com.jarvis.android.domain.model.FileItem
import com.jarvis.android.domain.model.FileStats
import com.jarvis.android.domain.repository.FileRepository
import com.jarvis.android.system.root.RootManager
import com.jarvis.android.system.root.RootShell
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class FileRepositoryImpl @Inject constructor(
    private val rootShell:   RootShell,
    private val rootManager: RootManager,
) : FileRepository {

    override suspend fun listDirectory(path: String, asRoot: Boolean): Result<List<FileItem>> =
        withContext(Dispatchers.IO) {
            runCatching {
                if (asRoot && rootManager.isRooted) {
                    listViaRoot(path)
                } else {
                    listViaJava(path)
                }
            }
        }

    private suspend fun listViaRoot(path: String): List<FileItem> {
        // Use `ls -lan` for numeric UIDs/GIDs; parse each line
        val result = rootShell.exec("ls -lan ${shellQuote(path)} 2>&1", asRoot = true)
        return result.stdout
            .filter { it.isNotBlank() && !it.startsWith("total") }
            .mapNotNull { parseLsLine(it, path) }
            .sortedWith(compareByDescending<FileItem> { it.isDirectory }.thenBy { it.name })
    }

    private fun listViaJava(path: String): List<FileItem> {
        val dir = File(path)
        if (!dir.exists()) throw IllegalArgumentException("Path does not exist: $path")
        if (!dir.isDirectory) throw IllegalArgumentException("Not a directory: $path")
        return (dir.listFiles() ?: emptyArray())
            .map { f ->
                FileItem(
                    path         = f.absolutePath,
                    name         = f.name,
                    isDirectory  = f.isDirectory,
                    isSymlink    = false,
                    sizeBytes    = if (f.isFile) f.length() else 0,
                    permissions  = buildPermissions(f),
                    owner        = "app",
                    group        = "app",
                    lastModified = f.lastModified(),
                )
            }
            .sortedWith(compareByDescending<FileItem> { it.isDirectory }.thenBy { it.name })
    }

    // ── Read ──────────────────────────────────────────────────────────────

    override suspend fun readFile(path: String, maxBytes: Int, asRoot: Boolean): Result<String> =
        withContext(Dispatchers.IO) {
            runCatching {
                if (asRoot && rootManager.isRooted) {
                    val r = rootShell.exec("cat ${shellQuote(path)}", asRoot = true)
                    if (!r.isSuccess) throw RuntimeException(r.stderr.joinToString("; "))
                    r.stdout.joinToString("\n").take(maxBytes)
                } else {
                    val f = File(path)
                    if (!f.exists()) throw IllegalArgumentException("Not found: $path")
                    if (!f.isFile) throw IllegalArgumentException("Not a file: $path")
                    f.readText(Charsets.UTF_8).take(maxBytes)
                }
            }
        }

    // ── Write ─────────────────────────────────────────────────────────────

    override suspend fun writeFile(
        path: String, content: String, append: Boolean, asRoot: Boolean,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        runCatching {
            if (asRoot && rootManager.isRooted) {
                val op = if (append) ">>" else ">"
                val r  = rootShell.exec(
                    "printf '%s' ${shellQuote(content)} $op ${shellQuote(path)}",
                    asRoot = true,
                )
                if (!r.isSuccess) throw RuntimeException(r.stderr.joinToString("; "))
            } else {
                val f = File(path)
                f.parentFile?.mkdirs()
                if (append) f.appendText(content, Charsets.UTF_8)
                else        f.writeText(content, Charsets.UTF_8)
            }
        }
    }

    // ── Delete ────────────────────────────────────────────────────────────

    override suspend fun deleteFile(path: String, asRoot: Boolean): Result<Unit> =
        withContext(Dispatchers.IO) {
            runCatching {
                if (asRoot && rootManager.isRooted) {
                    val r = rootShell.exec("rm -rf ${shellQuote(path)}", asRoot = true)
                    if (!r.isSuccess) throw RuntimeException(r.stderr.joinToString("; "))
                } else {
                    val f = File(path)
                    if (f.isDirectory) f.deleteRecursively() else f.delete()
                    Unit
                }
            }
        }

    // ── Move / copy ───────────────────────────────────────────────────────

    override suspend fun moveFile(from: String, to: String, asRoot: Boolean): Result<Unit> =
        withContext(Dispatchers.IO) {
            runCatching {
                if (asRoot && rootManager.isRooted) {
                    rootShell.exec("mv ${shellQuote(from)} ${shellQuote(to)}", asRoot = true)
                        .also { if (!it.isSuccess) throw RuntimeException(it.stderr.joinToString("; ")) }
                    Unit
                } else {
                    File(from).renameTo(File(to))
                        .also { if (!it) throw RuntimeException("rename failed") }
                    Unit
                }
            }
        }

    override suspend fun copyFile(from: String, to: String, asRoot: Boolean): Result<Unit> =
        withContext(Dispatchers.IO) {
            runCatching {
                if (asRoot && rootManager.isRooted) {
                    rootShell.exec("cp -r ${shellQuote(from)} ${shellQuote(to)}", asRoot = true)
                        .also { if (!it.isSuccess) throw RuntimeException(it.stderr.joinToString("; ")) }
                    Unit
                } else {
                    File(from).copyRecursively(File(to), overwrite = true)
                    Unit
                }
            }
        }

    // ── Stats ─────────────────────────────────────────────────────────────

    override suspend fun getStats(path: String): Result<FileStats> =
        withContext(Dispatchers.IO) {
            runCatching {
                val f    = File(path)
                val ext  = path.substringAfterLast('.', "").lowercase()
                val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
                FileStats(
                    path         = path,
                    sizeBytes    = f.length(),
                    isDirectory  = f.isDirectory,
                    isReadable   = f.canRead(),
                    isWritable   = f.canWrite(),
                    isExecutable = f.canExecute(),
                    lastModified = f.lastModified(),
                    mimeType     = mime,
                )
            }
        }

    // ── Mkdir ─────────────────────────────────────────────────────────────

    override suspend fun createDirectory(path: String, asRoot: Boolean): Result<Unit> =
        withContext(Dispatchers.IO) {
            runCatching {
                if (asRoot && rootManager.isRooted) {
                    rootShell.exec("mkdir -p ${shellQuote(path)}", asRoot = true)
                        .also { if (!it.isSuccess) throw RuntimeException(it.stderr.joinToString("; ")) }
                    Unit
                } else {
                    File(path).mkdirs()
                    Unit
                }
            }
        }

    // ── Parse helpers ─────────────────────────────────────────────────────

    /**
     * Parse a single `ls -lan` output line into a [FileItem].
     * Format: `-rwxr-xr-x 1 0 0 12345 2024-01-01 00:00 filename`
     */
    private fun parseLsLine(line: String, parent: String): FileItem? {
        return try {
            val parts = line.trim().split(Regex("\\s+"))
            if (parts.size < 8) return null

            val perms     = parts[0]
            val sizeStr   = parts[4]
            val dateStr   = "${parts[5]} ${parts[6]}"
            val name      = parts.drop(7).joinToString(" ").let {
                if (it.contains(" -> ")) it.substringBefore(" -> ") else it
            }
            val isSymlink = perms.startsWith("l")
            val isDir     = perms.startsWith("d")

            val ts = runCatching {
                SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).parse(dateStr)?.time ?: 0L
            }.getOrDefault(0L)

            FileItem(
                path         = "$parent/$name",
                name         = name,
                isDirectory  = isDir,
                isSymlink    = isSymlink,
                sizeBytes    = sizeStr.toLongOrNull() ?: 0,
                permissions  = perms.drop(1),    // strip type char
                owner        = parts[2],
                group        = parts[3],
                lastModified = ts,
            )
        } catch (_: Exception) { null }
    }

    private fun buildPermissions(f: File): String {
        val r = if (f.canRead()) "r" else "-"
        val w = if (f.canWrite()) "w" else "-"
        val x = if (f.canExecute()) "x" else "-"
        return "$r${w}${x}------"
    }

    private fun shellQuote(s: String) = "'${s.replace("'", "'\\''")}'"
}
