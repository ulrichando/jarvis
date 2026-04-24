package com.jarvis.android.system.bridge

import android.util.Log
import com.jarvis.android.domain.model.ChatEvent
import com.jarvis.android.domain.repository.ChatRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Loopback HTTP + SSE server that exposes the Jarvis agent loop (same one
 * the in-app chat uses — including all 23 tools from [JarvisToolDispatcher])
 * to the on-device terminal session. Bound only to 127.0.0.1 so no external
 * traffic can reach it.
 *
 * Endpoints:
 *   GET  /health            → 200 "ok"
 *   POST /chat              → text/event-stream of Jarvis ChatEvents
 *       body: {"prompt":"...", "conversationId":"...?", "model":"...?"}
 *
 * SSE event payloads (one JSON object per `data:` line):
 *   {"type":"session",  "conversationId":"…"}
 *   {"type":"text",     "content":"…"}
 *   {"type":"tool_start","id":"…","name":"…"}
 *   {"type":"tool_end", "id":"…","name":"…","result":"…","error":false}
 *   {"type":"warning",  "message":"…"}
 *   {"type":"error",    "message":"…"}
 *   {"type":"done"}
 *
 * Started in [com.jarvis.android.service.JarvisForegroundService.onCreate]
 * so it's alive as long as the service is. Stopped in onDestroy.
 */
@Singleton
class JarvisLoopbackServer @Inject constructor(
    private val chatRepository: ChatRepository,
) {

    companion object {
        /** Fixed port — the shell script hard-codes it. */
        const val PORT = 47811
        private const val TAG = "JarvisLoopback"
        private const val DEFAULT_MODEL = "openai/gpt-oss-120b"
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var serverSocket: ServerSocket? = null
    private var acceptJob: Job? = null

    fun start() {
        if (acceptJob?.isActive == true) return
        try {
            val ss = ServerSocket()
            ss.reuseAddress = true
            ss.bind(InetSocketAddress(InetAddress.getByName("127.0.0.1"), PORT))
            serverSocket = ss
            acceptJob = scope.launch {
                Log.i(TAG, "listening on 127.0.0.1:$PORT")
                while (isActive) {
                    val client = try { ss.accept() } catch (_: Throwable) { break }
                    launch { handle(client) }
                }
            }
        } catch (t: Throwable) {
            Log.e(TAG, "failed to start: ${t.message}", t)
        }
    }

    fun stop() {
        acceptJob?.cancel()
        runCatching { serverSocket?.close() }
        serverSocket = null
        scope.cancel()
    }

    // ── Request handler ───────────────────────────────────────────────────────

    private suspend fun handle(socket: Socket) = withContext(Dispatchers.IO) {
        try {
            socket.use { s ->
                val reader = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
                val out    = s.getOutputStream()

                val requestLine = reader.readLine() ?: return@use
                val parts       = requestLine.split(" ")
                if (parts.size < 2) { write404(out); return@use }
                val method = parts[0]
                val path   = parts[1].substringBefore('?')

                // Headers
                val headers = mutableMapOf<String, String>()
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                    val idx = line.indexOf(':')
                    if (idx > 0) headers[line.substring(0, idx).trim().lowercase()] =
                        line.substring(idx + 1).trim()
                }

                // Body (POST only)
                val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
                val body = if (contentLength > 0) {
                    val buf = CharArray(contentLength)
                    var read = 0
                    while (read < contentLength) {
                        val n = reader.read(buf, read, contentLength - read)
                        if (n < 0) break
                        read += n
                    }
                    String(buf, 0, read)
                } else ""

                when {
                    method == "GET"  && path == "/health" -> writeOk(out, "ok")
                    method == "POST" && path == "/chat"   -> handleChat(out, body)
                    else -> write404(out)
                }
            }
        } catch (t: Throwable) {
            Log.w(TAG, "handler error: ${t.message}")
        }
    }

    // ── /chat — SSE stream of ChatEvents ──────────────────────────────────────

    private suspend fun handleChat(out: OutputStream, body: String) {
        val prompt = jsonString(body, "prompt")
        val convIn = jsonString(body, "conversationId")
        val model  = jsonString(body, "model").ifBlank { DEFAULT_MODEL }

        if (prompt.isBlank()) {
            writeBad(out, "missing 'prompt' field")
            return
        }

        // Resolve or create a conversation so the agent loop has state.
        val conv = if (convIn.isNotBlank())
            runCatching { chatRepository.createConversation("Terminal", model) }.getOrNull()
                ?: chatRepository.createConversation("Terminal", model)
        else
            chatRepository.createConversation("Terminal", model)

        // SSE preamble
        out.write(
            ("HTTP/1.1 200 OK\r\n" +
             "Content-Type: text/event-stream; charset=utf-8\r\n" +
             "Cache-Control: no-cache\r\n" +
             "Connection: close\r\n" +
             "Access-Control-Allow-Origin: *\r\n" +
             "\r\n").toByteArray(Charsets.UTF_8)
        )
        out.flush()

        sse(out, """{"type":"session","conversationId":"${jsonEscape(conv.id)}"}""")

        try {
            chatRepository.sendMessage(conv.id, prompt)
                .catch { t ->
                    sse(out, """{"type":"error","message":"${jsonEscape(t.message ?: "unknown")}"}""")
                }
                .collect { ev ->
                    val line = when (ev) {
                        is ChatEvent.TextDelta ->
                            """{"type":"text","content":"${jsonEscape(ev.text)}"}"""
                        is ChatEvent.ToolCallStarted ->
                            """{"type":"tool_start","id":"${jsonEscape(ev.toolId)}","name":"${jsonEscape(ev.toolName)}"}"""
                        is ChatEvent.ToolCallCompleted ->
                            """{"type":"tool_end","id":"${jsonEscape(ev.toolId)}","name":"${jsonEscape(ev.toolName)}","result":"${jsonEscape(ev.result)}","error":${ev.isError}}"""
                        is ChatEvent.ConfirmationNeeded ->
                            """{"type":"confirm_needed","tool":"${jsonEscape(ev.request.toolName)}","message":"${jsonEscape(ev.request.description)}"}"""
                        is ChatEvent.TurnSaved ->
                            """{"type":"turn_saved","id":${ev.messageId}}"""
                        is ChatEvent.Warning ->
                            """{"type":"warning","message":"${jsonEscape(ev.message)}"}"""
                        is ChatEvent.Error ->
                            """{"type":"error","message":"${jsonEscape(ev.message)}"}"""
                        ChatEvent.Done ->
                            """{"type":"done"}"""
                    }
                    sse(out, line)
                }
        } catch (t: Throwable) {
            sse(out, """{"type":"error","message":"${jsonEscape(t.message ?: "collect failed")}"}""")
        }
    }

    // ── HTTP plumbing ────────────────────────────────────────────────────────

    private fun sse(out: OutputStream, json: String) {
        runCatching {
            out.write(("data: $json\n\n").toByteArray(Charsets.UTF_8))
            out.flush()
        }
    }

    private fun writeOk(out: OutputStream, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        val head = "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n" +
                   "Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
        out.write(head.toByteArray(Charsets.UTF_8)); out.write(bytes); out.flush()
    }

    private fun write404(out: OutputStream) {
        val head = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        out.write(head.toByteArray(Charsets.UTF_8)); out.flush()
    }

    private fun writeBad(out: OutputStream, msg: String) {
        val bytes = msg.toByteArray(Charsets.UTF_8)
        val head = "HTTP/1.1 400 Bad Request\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
        out.write(head.toByteArray(Charsets.UTF_8)); out.write(bytes); out.flush()
    }

    // ── Minimal JSON helpers (no third-party dep; bodies are small) ──────────

    /**
     * Extract a top-level string field from a shallow JSON body.
     * Only handles the flat {"field":"value",...} shape the shell script
     * sends — we control the client, so this is sufficient.
     */
    private fun jsonString(body: String, field: String): String {
        val needle = "\"$field\""
        var i = body.indexOf(needle)
        if (i < 0) return ""
        i += needle.length
        // Skip whitespace + colon
        while (i < body.length && body[i] != ':') i++
        if (i >= body.length) return ""
        i++
        while (i < body.length && body[i].isWhitespace()) i++
        if (i >= body.length || body[i] != '"') return ""
        i++
        val sb = StringBuilder()
        while (i < body.length) {
            val c = body[i]
            if (c == '\\') {
                if (i + 1 >= body.length) break
                when (val n = body[i + 1]) {
                    'n' -> sb.append('\n')
                    't' -> sb.append('\t')
                    'r' -> sb.append('\r')
                    '"' -> sb.append('"')
                    '\\' -> sb.append('\\')
                    else -> sb.append(n)
                }
                i += 2
            } else if (c == '"') {
                return sb.toString()
            } else {
                sb.append(c); i++
            }
        }
        return sb.toString()
    }

    private fun jsonEscape(s: String): String {
        val sb = StringBuilder(s.length + 16)
        for (c in s) {
            when (c) {
                '\\' -> sb.append("\\\\")
                '"'  -> sb.append("\\\"")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> {
                    if (c.code < 0x20) sb.append("\\u%04x".format(c.code))
                    else sb.append(c)
                }
            }
        }
        return sb.toString()
    }
}
