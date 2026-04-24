package com.jarvis.android.util

import android.content.Context
import android.net.Uri
import android.util.Log
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.text.PDFTextStripper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Extract plain-text content from picked documents so the chat input can
 * receive something the LLM actually understands. The contracts here are
 * deliberately small — one result object with either text, a friendly
 * error, or a "too big to paste" truncation flag — because the caller
 * (ChatScreen file picker) has to surface the outcome as a single toast.
 */
sealed class DocumentExtractResult {
    /** Successful extraction. `text` is already capped at the paste limit. */
    data class Ok(val text: String, val pageCount: Int, val truncated: Boolean) :
        DocumentExtractResult()

    /** File was a format we don't extract (locked, encrypted, scanned-only PDF, …). */
    data class Unsupported(val reason: String) : DocumentExtractResult()

    /** IO / parse failure. `cause` is already logged for diagnosis. */
    data class Failed(val cause: String) : DocumentExtractResult()
}

object DocumentExtractor {

    private const val TAG            = "DocumentExtractor"
    private const val PASTE_CAP_CHARS = 128 * 1024   // keep parity with the
                                                    // text-file paste path.

    /**
     * Extract text from a PDF at [uri]. Runs on [Dispatchers.IO] because
     * PDFBox is heavily blocking (zlib decompression, font parsing, glyph
     * tracing). Caps the pasted body at [PASTE_CAP_CHARS] so a 500-page
     * report doesn't nuke the input field.
     */
    suspend fun extractPdf(ctx: Context, uri: Uri): DocumentExtractResult =
        withContext(Dispatchers.IO) {
            val stream = runCatching {
                ctx.contentResolver.openInputStream(uri)
            }.getOrNull() ?: return@withContext DocumentExtractResult.Failed(
                "Couldn't open the file",
            )

            stream.use { input ->
                try {
                    PDDocument.load(input).use { doc ->
                        if (doc.isEncrypted) {
                            return@withContext DocumentExtractResult.Unsupported(
                                "PDF is password-protected",
                            )
                        }
                        val pageCount = doc.numberOfPages
                        val stripper  = PDFTextStripper().apply {
                            // Page-by-page so we can early-stop once we hit
                            // the paste cap — otherwise we'd decode 500
                            // pages just to throw the tail away.
                            sortByPosition = true
                        }
                        val sb = StringBuilder()
                        for (p in 1..pageCount) {
                            stripper.startPage = p
                            stripper.endPage   = p
                            val pageText = runCatching { stripper.getText(doc) }
                                .getOrDefault("")
                            sb.append(pageText)
                            if (sb.length >= PASTE_CAP_CHARS) break
                        }
                        val truncated = sb.length >= PASTE_CAP_CHARS
                        val body = if (truncated) sb.substring(0, PASTE_CAP_CHARS) else sb.toString()
                        if (body.isBlank()) {
                            DocumentExtractResult.Unsupported(
                                "PDF has no extractable text (likely a scanned image)",
                            )
                        } else {
                            DocumentExtractResult.Ok(body, pageCount, truncated)
                        }
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "PDF extract failed", e)
                    DocumentExtractResult.Failed(e.message ?: "parse error")
                }
            }
        }
}
