package com.jarvis.android.system.llm

import android.util.Log
import com.google.ai.edge.litertlm.Tool
import com.google.ai.edge.litertlm.ToolParam
import com.google.ai.edge.litertlm.ToolSet
import com.jarvis.android.system.tools.JarvisToolDispatcher
import com.jarvis.android.system.tools.ToolUseBlock
import dagger.Lazy
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Adapts jarvis's [JarvisToolDispatcher] into a LiteRT-LM [ToolSet].
 *
 * LiteRT-LM discovers callable tools by reflecting over methods annotated
 * with [@Tool]. Each param annotated with [@ToolParam] becomes a field in
 * the JSON-schema function declaration the runtime sends to the model.
 * When Gemma 4 / Gemma 3n / DeepSeek-R1 emits a structured tool call, the
 * runtime deserialises the arguments, invokes the matching method, and
 * feeds the return value (serialised to a `Map<String, ...>`) back into
 * the conversation as a tool result — same loop the Anthropic API runs.
 *
 * ## Scope
 *
 * This first cut exposes three read-only tools so we can validate the end-
 * to-end flow without risking anything destructive:
 *   - [readFile] — cat a file path (honours [JarvisToolDispatcher]'s root-
 *     aware read so it works on /data, /system, etc. when root is granted)
 *   - [listDirectory] — directory listing (single level)
 *   - [getSystemInfo] — CPU/RAM/battery snapshot
 *
 * Once these work end-to-end we'll expand to the write_file, bash_exec,
 * and network_scan tools — each behind the existing confirmation gate for
 * the destructive ones.
 *
 * ## Blocking caveat
 *
 * LiteRT-LM invokes tool methods synchronously on its inference thread.
 * [JarvisToolDispatcher.dispatch] is a suspend fun, so we use [runBlocking]
 * to bridge — same pattern Google's AI Edge Gallery uses in its AgentTools.
 * Long-running tool calls therefore pause generation; keep tool work quick.
 */
@Singleton
class JarvisLiteRtTools @Inject constructor(
    // Lazy<> breaks the otherwise-circular DI graph:
    //   ModelRepository ← JarvisToolDispatcher ← JarvisLiteRtTools ←
    //   LiteRtLmBackend ← ModelRepositoryImpl (== ModelRepository).
    // Lazy.get() is called only at tool-invocation time, so the provider
    // doesn't need to be constructed during DI graph setup.
    private val dispatcherLazy: Lazy<JarvisToolDispatcher>,
) : ToolSet {

    private val dispatcher: JarvisToolDispatcher
        get() = dispatcherLazy.get()

    // ── Tool 1: read_file ────────────────────────────────────────────────────

    @Tool(description = "Read the full text contents of a file on the device. " +
                        "Works for any path the app can access (and /data, /system " +
                        "when root is granted via Magisk/KernelSU).")
    fun readFile(
        @ToolParam(description = "Absolute file path, e.g. /sdcard/Download/notes.txt")
        path: String,
    ): Map<String, String> = runJarvis(
        name  = "read_file",
        input = buildJsonObject { put("path", path) },
    )

    // ── Tool 2: list_directory ───────────────────────────────────────────────

    @Tool(description = "List the immediate children of a directory on the device. " +
                        "Returns file names, sizes, and mode bits.")
    fun listDirectory(
        @ToolParam(description = "Absolute directory path, e.g. /sdcard/Download")
        path: String,
    ): Map<String, String> = runJarvis(
        name  = "list_directory",
        input = buildJsonObject { put("path", path) },
    )

    // ── Tool 3: get_system_info ──────────────────────────────────────────────

    @Tool(description = "Get a snapshot of the device: CPU model, RAM total/free, " +
                        "battery level, uptime, Android version, kernel release.")
    fun getSystemInfo(): Map<String, String> = runJarvis(
        name  = "get_system_info",
        input = buildJsonObject {  /* no args */ },
    )

    // ── Bridge to the async dispatcher ───────────────────────────────────────

    /**
     * Shared call path for every tool: build the [ToolUseBlock] the dispatcher
     * expects, block on [JarvisToolDispatcher.dispatch] (LiteRT-LM's invoker is
     * synchronous), and return the result in the `{ content, is_error }` shape
     * the runtime feeds back to the model.
     */
    private fun runJarvis(name: String, input: JsonObject): Map<String, String> =
        runBlocking(Dispatchers.Default) {
            val result = try {
                dispatcher.dispatch(
                    ToolUseBlock(
                        id    = UUID.randomUUID().toString(),
                        name  = name,
                        input = input,
                    )
                )
            } catch (t: Throwable) {
                Log.e(TAG, "Tool '$name' threw: ${t.message}", t)
                return@runBlocking mapOf(
                    "content" to (t.message ?: "unknown error"),
                    "is_error" to "true",
                )
            }
            mapOf(
                "content"  to result.content,
                "is_error" to result.isError.toString(),
            )
        }

    companion object {
        private const val TAG = "JarvisLiteRtTools"
    }
}
