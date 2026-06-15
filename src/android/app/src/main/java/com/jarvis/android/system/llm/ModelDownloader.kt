package com.jarvis.android.system.llm

import android.content.Context
import android.util.Log
import androidx.hilt.work.HiltWorker
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.jarvis.android.data.local.dao.ModelDao
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import com.jarvis.android.data.repository.ModelDownloaderService
import com.jarvis.android.domain.model.ModelEntry
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.RandomAccessFile
import java.net.URI
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * WorkManager-backed implementation of [ModelDownloaderService].
 *
 * ## Resume behaviour
 *
 * If a `.tmp` file from a previous attempt exists, the download resumes from
 * the byte offset where it left off by sending a `Range: bytes=<offset>-` HTTP
 * header. If the server responds with 200 (no range support) the partial file is
 * discarded and the download restarts.
 *
 * ## Work uniqueness
 *
 * Each model is enqueued as a unique job named `download_<modelId>`.
 * [ExistingWorkPolicy.KEEP] prevents duplicate downloads if [enqueue] is called
 * twice before the job completes.
 *
 * ## Cancellation
 *
 * [cancel] calls [WorkManager.cancelUniqueWork] and resets the DB row to
 * `NOT_DOWNLOADED`. Partial `.tmp` files are cleaned up in the worker's
 * catch block.
 */
@Singleton
class ModelDownloader @Inject constructor(
    @ApplicationContext private val context: Context,
    private val workManager: WorkManager,
) : ModelDownloaderService {

    override suspend fun enqueue(entry: ModelEntry) {
        val destFile = modelFile(context, entry.id, entry.downloadUrl)

        val inputData: Data = workDataOf(
            DownloadWorker.KEY_MODEL_ID    to entry.id,
            DownloadWorker.KEY_DOWNLOAD_URL to entry.downloadUrl,
            DownloadWorker.KEY_SHA256      to entry.sha256,
            DownloadWorker.KEY_DEST_PATH   to destFile.absolutePath,
        )

        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(inputData)
            .setConstraints(constraints)
            .addTag(entry.id)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()

        workManager.enqueueUniqueWork(
            workName(entry.id),
            ExistingWorkPolicy.KEEP,
            request,
        )
        Log.i(TAG, "Enqueued download: ${entry.id} → ${destFile.absolutePath}")
    }

    override suspend fun cancel(modelId: String) {
        workManager.cancelUniqueWork(workName(modelId))
        Log.i(TAG, "Cancelled download: $modelId")
        // Partial file cleanup is handled by the worker's finally block.
        // The repository handles DB reset via markDeleted().
    }

    companion object {
        private const val TAG = "ModelDownloader"

        private fun workName(modelId: String) = "download_$modelId"

        /**
         * Derive the destination file path from the model ID and download URL.
         * Files land in `<externalFilesDir>/models/<modelId>.<ext>`.
         */
        fun modelFile(context: Context, modelId: String, downloadUrl: String): File {
            val ext = downloadUrl.substringAfterLast('.', "bin")
                .takeIf { it.length in 1..8 } ?: "bin"
            val dir = context.getExternalFilesDir("models")
                ?: context.filesDir.resolve("models")
            dir.mkdirs()
            return File(dir, "$modelId.$ext")
        }
    }
}

// ── Worker ─────────────────────────────────────────────────────────────────────

/**
 * Background download worker.
 *
 * Handles:
 *   - Resumable HTTP via `Range` header (falls back to full re-download if server
 *     returns 200 instead of 206).
 *   - SHA-256 integrity verification before promoting the temp file.
 *   - WorkManager progress reporting (`PROGRESS_BYTES`, `PROGRESS_TOTAL`).
 *   - DB state transitions: `DOWNLOADED` on success, `FAILED:<reason>` on error.
 *
 * Retries are handled by WorkManager (exponential backoff, max 3 attempts).
 */
@HiltWorker
class DownloadWorker @AssistedInject constructor(
    @Assisted private val appContext: Context,
    @Assisted params: WorkerParameters,
    private val modelDao: ModelDao,
    private val apiKeyProvider: ApiKeyProviderImpl,
) : CoroutineWorker(appContext, params) {

    /**
     * Attach `Authorization: Bearer <token>` when the URL host is huggingface.co
     * and a token is configured. Gated HF repos (Gemma, Llama, some Mistral
     * variants) return 401 for anonymous requests even when the URL is public.
     * No-op for non-HF hosts so third-party CDNs (CloudFront redirects, public
     * GGUF mirrors) aren't sent a stray credential.
     */
    private fun Request.Builder.attachHfAuthIfNeeded(url: String): Request.Builder {
        val host = runCatching { URI(url).host }.getOrNull() ?: return this
        if (!host.equals("huggingface.co", ignoreCase = true) &&
            !host.endsWith(".huggingface.co", ignoreCase = true)) return this
        val token = apiKeyProvider.getHfToken()
        if (token.isBlank()) return this
        return header("Authorization", "Bearer $token")
    }

    override suspend fun doWork(): Result {
        val modelId     = inputData.getString(KEY_MODEL_ID)     ?: return Result.failure()
        val downloadUrl = inputData.getString(KEY_DOWNLOAD_URL) ?: return Result.failure()
        val sha256      = inputData.getString(KEY_SHA256)       ?: ""
        val destPath    = inputData.getString(KEY_DEST_PATH)    ?: return Result.failure()

        val destFile = File(destPath)
        val tmpFile  = File("$destPath.tmp")

        Log.i(TAG, "Starting download: $modelId from $downloadUrl")

        return try {
            // Mark as in-progress immediately so the UI shows the progress bar
            modelDao.markDownloading(modelId, 0f)
            // Try the fast path first — multi-connection parallel range
            // downloads. Falls through to the single-stream resumable path
            // if the server or partial file blocks parallelism.
            val parallelSucceeded = tryParallelDownload(modelId, downloadUrl, tmpFile)
            if (!parallelSucceeded) {
                Log.i(TAG, "Parallel download unavailable for $modelId — using single-stream resume")
                downloadWithResume(modelId, downloadUrl, tmpFile)
            }

            // Verify integrity before promoting (only when the catalog pins a hash)
            if (sha256.isNotEmpty()) {
                Log.i(TAG, "Verifying SHA-256 for $modelId…")
                val computed = computeSha256(tmpFile)
                if (!computed.equals(sha256, ignoreCase = true)) {
                    tmpFile.delete()
                    val msg = "SHA-256 mismatch: expected $sha256, got $computed"
                    Log.e(TAG, msg)
                    modelDao.markFailed(modelId, msg)
                    return Result.failure(workDataOf(KEY_ERROR to msg))
                }
            }

            // Promote temp → final
            if (destFile.exists()) destFile.delete()
            tmpFile.renameTo(destFile)

            modelDao.markDownloaded(modelId, destFile.absolutePath)
            Log.i(TAG, "Download complete: $modelId → ${destFile.absolutePath}")
            Result.success()

        } catch (e: PermanentDownloadException) {
            // 4xx or other non-retryable error — fail immediately without burning retries
            val msg = e.message ?: "Permanent download error"
            Log.e(TAG, "Permanent download error for $modelId: $msg")
            tmpFile.delete()
            modelDao.markFailed(modelId, msg)
            Result.failure(workDataOf(KEY_ERROR to msg))
        } catch (e: Exception) {
            val msg = e.message ?: "Download failed"
            Log.e(TAG, "Download error for $modelId: $msg", e)

            // Transient errors (network timeout, 5xx) — retry with backoff; keep temp for resume
            if (runAttemptCount >= MAX_RETRIES) {
                tmpFile.delete()
                modelDao.markFailed(modelId, msg)
                Result.failure(workDataOf(KEY_ERROR to msg))
            } else {
                Result.retry()
            }
        }
    }

    // ── Parallel range download (fast path) ───────────────────────────────────
    //
    // Mobile networks (LTE, mid-range Wi-Fi) are far from saturated by a single
    // HTTP connection — HuggingFace's per-connection throttle plus TCP's
    // conservative window scaling on cell radios leaves most of the pipe
    // unused. Splitting the body into N equal byte ranges and pulling them
    // concurrently uses the full radio and typically yields 3-5× throughput.
    //
    // Implementation:
    //   1. HEAD request to discover total size and confirm Accept-Ranges.
    //   2. Split [0, total) into [CONCURRENT_CONNECTIONS] equal slices.
    //   3. One coroutine per slice, each writes to its own offset inside the
    //      same RandomAccessFile via seek/write. RandomAccessFile is
    //      thread-safe for non-overlapping offsets.
    //   4. Shared AtomicLong tracks total bytes received for progress.
    //
    // Fallback conditions (return false → caller uses downloadWithResume):
    //   - HEAD fails or returns non-200
    //   - Server doesn't advertise 'Accept-Ranges: bytes'
    //   - Total size unknown
    //   - A partial `.tmp` exists (resume the single-stream path instead —
    //     we don't try to merge partial state with parallel slicing)

    private suspend fun tryParallelDownload(
        modelId:     String,
        downloadUrl: String,
        tmpFile:     File,
    ): Boolean {
        // Skip parallel if a partial file exists — let the single-stream
        // path resume it cleanly.
        if (tmpFile.exists() && tmpFile.length() > 0L) return false

        // HEAD to discover size + range support. Some CDNs reject HEAD
        // (Cloudflare in particular) — treat any non-success as "no fast
        // path available" and fall through.
        val headResp = try {
            httpClient.newCall(
                Request.Builder()
                    .url(downloadUrl)
                    .head()
                    .header("User-Agent", USER_AGENT)
                    .header("Accept",      "*/*")
                    .attachHfAuthIfNeeded(downloadUrl)
                    .build()
            ).execute()
        } catch (_: Exception) { return false }

        val totalBytes = headResp.use { resp ->
            if (!resp.isSuccessful) return false
            val acceptsRanges = resp.header("Accept-Ranges")?.contains("bytes", ignoreCase = true) == true
            val len = resp.header("Content-Length")?.toLongOrNull() ?: -1L
            if (!acceptsRanges || len <= 0L) return false
            len
        }

        Log.i(TAG, "Parallel download: $modelId $totalBytes bytes across $CONCURRENT_CONNECTIONS connections")

        // Pre-allocate so each writer can seek without interleaving.
        RandomAccessFile(tmpFile, "rw").use { it.setLength(totalBytes) }

        val receivedBytes = java.util.concurrent.atomic.AtomicLong(0L)
        val lastProgress  = java.util.concurrent.atomic.AtomicLong(0L)
        val chunkSize     = (totalBytes + CONCURRENT_CONNECTIONS - 1) / CONCURRENT_CONNECTIONS

        try {
            coroutineScope {
                val jobs = (0 until CONCURRENT_CONNECTIONS).map { idx ->
                    async(Dispatchers.IO) {
                        val start = idx * chunkSize
                        val end   = minOf(start + chunkSize, totalBytes) - 1
                        if (start >= totalBytes) return@async

                        val req = Request.Builder()
                            .url(downloadUrl)
                            .header("User-Agent", USER_AGENT)
                            .header("Accept",      "*/*")
                            .header("Range",       "bytes=$start-$end")
                            .attachHfAuthIfNeeded(downloadUrl)
                            .build()

                        httpClient.newCall(req).execute().use { resp ->
                            if (resp.code != 206) {
                                // Server didn't honour the range — bail out and
                                // let the caller fall back to single-stream.
                                throw IllegalStateException("Slice $idx: expected 206, got ${resp.code}")
                            }
                            val body   = resp.body ?: error("Empty body for slice $idx")
                            val buffer = ByteArray(BUFFER_SIZE)
                            RandomAccessFile(tmpFile, "rw").use { raf ->
                                raf.seek(start)
                                body.byteStream().use { input ->
                                    var read: Int
                                    while (input.read(buffer).also { read = it } != -1) {
                                        raf.write(buffer, 0, read)
                                        val totalRx = receivedBytes.addAndGet(read.toLong())

                                        // CAS on lastProgress so at most one
                                        // writer reports progress per window.
                                        // markDownloading is a suspend fn so
                                        // it can't live in a sync block — the
                                        // CAS serialises "who reports now"
                                        // without locking across the suspend.
                                        val now  = System.currentTimeMillis()
                                        val last = lastProgress.get()
                                        if (now - last >= PROGRESS_INTERVAL_MS &&
                                            lastProgress.compareAndSet(last, now)) {
                                            val pct = totalRx.toFloat() / totalBytes
                                            setProgressAsync(
                                                workDataOf(
                                                    KEY_PROGRESS       to pct,
                                                    KEY_PROGRESS_BYTES to totalRx,
                                                    KEY_PROGRESS_TOTAL to totalBytes,
                                                )
                                            )
                                            modelDao.markDownloading(modelId, pct)
                                            Log.v(TAG, "$modelId parallel: $totalRx / $totalBytes (${(pct * 100).toInt()}%)")
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                jobs.awaitAll()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Parallel download failed for $modelId (${e.message}) — falling back")
            if (tmpFile.exists()) tmpFile.delete()
            return false
        }

        // Silent-hole guard. The file was pre-allocated with setLength(totalBytes)
        // so tmpFile.length() is useless as a completeness check — it always
        // matches. receivedBytes is the authoritative total of bytes actually
        // written across all slices. If a slice's HTTP stream returned EOF
        // early without throwing (mobile radio dropouts do this), awaitAll
        // completes "successfully" but the pre-allocated zeros remain where
        // the slice should have written weights. llama.cpp then SIGSEGVs on
        // mmap. Reject before promoting so the user re-downloads.
        val rx = receivedBytes.get()
        if (rx != totalBytes) {
            Log.w(TAG, "Parallel download incomplete: wrote $rx / $totalBytes bytes — falling back")
            if (tmpFile.exists()) tmpFile.delete()
            return false
        }
        return true
    }

    // ── Resumable download ────────────────────────────────────────────────────

    private suspend fun downloadWithResume(
        modelId:     String,
        downloadUrl: String,
        tmpFile:     File,
    ) {
        val resumeOffset = if (tmpFile.exists()) tmpFile.length() else 0L

        // HuggingFace (and several CDNs) quietly reject requests with no
        // User-Agent or with stock "okhttp/..." as bots — the 403 is silent
        // and impossible to debug from the app side. Send a stable UA + an
        // Accept header so the request looks like a real client.
        val request = Request.Builder()
            .url(downloadUrl)
            .header("User-Agent", USER_AGENT)
            .header("Accept",      "*/*")
            .apply { if (resumeOffset > 0L) header("Range", "bytes=$resumeOffset-") }
            .attachHfAuthIfNeeded(downloadUrl)
            .build()

        val response = httpClient.newCall(request).execute()
        response.use { resp ->
            if (!resp.isSuccessful && resp.code != 206) {
                // 4xx = permanent failure (auth / not found / gone) — don't retry
                if (resp.code in 400..499) {
                    throw PermanentDownloadException("HTTP ${resp.code} for $downloadUrl — check URL or authentication")
                }
                error("HTTP ${resp.code} for $downloadUrl")
            }

            // If server ignores Range (returns 200), restart from zero
            val appendMode = resp.code == 206 && resumeOffset > 0L
            if (!appendMode && tmpFile.exists()) tmpFile.delete()

            val totalBytes: Long = if (appendMode) {
                // Content-Range: bytes <start>-<end>/<total>
                resp.header("Content-Range")
                    ?.substringAfterLast('/')
                    ?.toLongOrNull()
                    ?: -1L
            } else {
                resp.body?.contentLength() ?: -1L
            }

            val body = resp.body ?: error("Empty response body")
            var downloadedBytes = if (appendMode) resumeOffset else 0L

            RandomAccessFile(tmpFile, "rw").use { raf ->
                if (appendMode) raf.seek(resumeOffset)
                val buffer = ByteArray(BUFFER_SIZE)
                body.byteStream().use { input ->
                    var read: Int
                    var lastProgressReport = 0L
                    while (input.read(buffer).also { read = it } != -1) {
                        raf.write(buffer, 0, read)
                        downloadedBytes += read

                        // Report progress at most once per second
                        val now = System.currentTimeMillis()
                        if (now - lastProgressReport >= PROGRESS_INTERVAL_MS) {
                            lastProgressReport = now
                            val progress = if (totalBytes > 0) {
                                (downloadedBytes.toFloat() / totalBytes).coerceIn(0f, 1f)
                            } else 0f
                            setProgressAsync(
                                workDataOf(
                                    KEY_PROGRESS       to progress,
                                    KEY_PROGRESS_BYTES to downloadedBytes,
                                    KEY_PROGRESS_TOTAL to totalBytes,
                                )
                            )
                            // Update DB so Room Flow (and the UI) reflects live progress
                            modelDao.markDownloading(modelId, progress)
                            Log.v(TAG, "$modelId: $downloadedBytes / $totalBytes bytes (${(progress * 100).toInt()}%)")
                        }
                    }
                }
            }

            // Completeness check — the stream returning -1 without throwing means
            // "EOF from the server's perspective", which on mobile can happen
            // from a silent radio-level drop. If the server told us how many
            // bytes to expect (Content-Length / Content-Range), enforce it so
            // we don't promote a truncated GGUF. totalBytes < 0 means chunked
            // transfer with no known size — can't verify, so we trust the loop.
            if (totalBytes > 0L && downloadedBytes != totalBytes) {
                throw IllegalStateException(
                    "Download truncated: wrote $downloadedBytes of $totalBytes bytes " +
                    "(server closed connection early) — will retry"
                )
            }
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    companion object {
        private const val TAG = "DownloadWorker"

        // WorkData keys — input
        const val KEY_MODEL_ID     = "model_id"
        const val KEY_DOWNLOAD_URL = "download_url"
        const val KEY_SHA256       = "sha256"
        const val KEY_DEST_PATH    = "dest_path"

        // WorkData keys — progress output
        const val KEY_PROGRESS       = "progress"         // Float 0.0–1.0, -1 if unknown
        const val KEY_PROGRESS_BYTES = "progress_bytes"   // Long — bytes received
        const val KEY_PROGRESS_TOTAL = "progress_total"   // Long — total bytes, -1 if unknown

        // WorkData keys — failure output
        const val KEY_ERROR = "error"

        private const val MAX_RETRIES           = 3
        // 256 KB buffers: ~4× fewer syscalls than 64 KB, still small enough
        // to keep memory per-connection reasonable on low-end devices.
        private const val BUFFER_SIZE           = 256 * 1_024
        private const val PROGRESS_INTERVAL_MS  = 1_000L
        // Empirically, 4 parallel range requests nearly max out mobile radios
        // without thrashing the CDN. HuggingFace tolerates this comfortably.
        // Going higher (8+) typically hits per-IP rate limits and slows down.
        private const val CONCURRENT_CONNECTIONS = 4

        // HuggingFace LFS endpoints redirect to CloudFront; both legs must be
        // followed. Multi-GB downloads also need a generous read timeout so a
        // slow cellular network doesn't poison a healthy connection.
        private const val USER_AGENT = "JARVIS-Android/1.0 (Kotlin; OkHttp)"

        private val httpClient: OkHttpClient by lazy {
            OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(600, TimeUnit.SECONDS)   // 10 min between bytes
                .followRedirects(true)
                .followSslRedirects(true)
                .retryOnConnectionFailure(true)
                // Dedicated dispatcher with a generous per-host cap so the
                // parallel-range downloads don't queue behind each other.
                .dispatcher(okhttp3.Dispatcher().apply {
                    maxRequests      = 16
                    maxRequestsPerHost = 16
                })
                .build()
        }
    }
}

/** Thrown for HTTP 4xx responses — these are permanent failures, never retried. */
private class PermanentDownloadException(message: String) : Exception(message)

// ── SHA-256 ────────────────────────────────────────────────────────────────────

/**
 * Compute the SHA-256 hex digest of [file].
 * Reads the file in 64 KB chunks to avoid loading large models into memory.
 */
private fun computeSha256(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    val buffer = ByteArray(64 * 1_024)
    file.inputStream().use { input ->
        var read: Int
        while (input.read(buffer).also { read = it } != -1) {
            digest.update(buffer, 0, read)
        }
    }
    return digest.digest().joinToString("") { "%02x".format(it) }
}
