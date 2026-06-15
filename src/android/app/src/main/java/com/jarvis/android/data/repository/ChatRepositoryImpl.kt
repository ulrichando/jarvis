package com.jarvis.android.data.repository

import android.content.Context
import android.util.Log
import com.jarvis.android.data.api.AssistantTurn
import com.jarvis.android.data.api.BrainApiService
import com.jarvis.android.data.api.ClaudeApiService
import com.jarvis.android.data.api.OpenAiCompatApiService
import com.jarvis.android.data.api.ToolDefinitions
import com.jarvis.android.domain.model.CloudModel
import com.jarvis.android.domain.model.CloudProvider
import com.jarvis.android.data.api.dto.ContentBlockDto
import com.jarvis.android.data.api.dto.ContentDelta
import com.jarvis.android.data.api.dto.MessageDto
import com.jarvis.android.data.api.dto.SseStreamEvent
import com.jarvis.android.data.local.dao.ConversationDao
import com.jarvis.android.data.local.dao.MessageDao
import com.jarvis.android.data.local.entity.ConversationEntity
import com.jarvis.android.data.local.entity.MessageEntity
import com.jarvis.android.domain.model.ChatEvent
import com.jarvis.android.domain.model.Conversation
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.model.MessageContentType
import com.jarvis.android.domain.model.MessageRole
import com.jarvis.android.domain.repository.ChatRepository
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.system.llm.Backend
import com.jarvis.android.system.llm.GenerationConfig
import com.jarvis.android.system.llm.IntelliRouter
import com.jarvis.android.system.tools.JarvisToolDispatcher
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Implements [ChatRepository] — the bridge between the UI and the Claude API.
 *
 * ## Agent loop (inside [sendMessage])
 * ```
 * repeat up to MAX_TOOL_ITERATIONS:
 *   1. Stream assistant turn from Claude
 *   2. Emit TextDelta events → UI appends tokens in real time
 *   3. If turn.stopReason == "tool_use":
 *        a. Emit ToolCallStarted per tool
 *        b. JarvisToolDispatcher.dispatch(toolUse) → ToolResultBlock
 *        c. Emit ToolCallCompleted
 *        d. Append assistant + tool_result messages to history
 *        e. Loop back to step 1
 *   4. If stop_reason == "end_turn" (or no tool_use): break
 * 5. Persist final assistant message, bump conversation stats
 * 6. Emit TurnSaved, then Done
 * ```
 *
 * All persistence is append-only — messages are never mutated after insertion.
 */
@Singleton
class ChatRepositoryImpl @Inject constructor(
    @ApplicationContext private val context: Context,
    private val conversationDao:    ConversationDao,
    private val messageDao:         MessageDao,
    private val claudeApi:          ClaudeApiService,
    private val brainApi:           BrainApiService,
    private val openAiCompatApi:    OpenAiCompatApiService,
    private val apiKeyProviderImpl: ApiKeyProviderImpl,
    private val toolDispatcher:     JarvisToolDispatcher,
    private val intelliRouter:      IntelliRouter,
    private val modelRepository:    ModelRepository,
) : ChatRepository {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }

    // ── System prompt (loaded once from assets) ───────────────────────────

    private val systemPrompt: String by lazy {
        try {
            context.assets.open("jarvis_persona.txt")
                .bufferedReader()
                .readText()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to load jarvis_persona.txt: ${e.message}")
            "You are JARVIS, an AI assistant running on a rooted Android device."
        }
    }

    // ── Conversation CRUD ─────────────────────────────────────────────────

    override fun observeConversations(): Flow<List<Conversation>> =
        conversationDao.observeAll().map { list -> list.map { it.toDomain() } }

    override suspend fun getConversation(id: String): Conversation? =
        conversationDao.getById(id)?.toDomain()

    override suspend fun createConversation(title: String, model: String): Conversation {
        val entity = ConversationEntity(
            id        = UUID.randomUUID().toString(),
            title     = title,
            model     = model,
            createdAt = System.currentTimeMillis(),
            updatedAt = System.currentTimeMillis(),
        )
        conversationDao.insert(entity)
        return entity.toDomain()
    }

    override suspend fun renameConversation(id: String, title: String) =
        conversationDao.updateTitle(id, title)

    override suspend fun pinConversation(id: String, pinned: Boolean) =
        conversationDao.setPinned(id, pinned)

    override suspend fun deleteConversation(id: String) =
        conversationDao.deleteById(id)

    override suspend fun deleteAllConversations() =
        conversationDao.deleteAll()

    // ── Message reads ─────────────────────────────────────────────────────

    override fun observeMessages(conversationId: String): Flow<List<Message>> =
        messageDao.observeByConversation(conversationId).map { list -> list.map { it.toDomain() } }

    override suspend fun getRecentMessages(conversationId: String, limit: Int): List<Message> =
        messageDao.getRecentByConversation(conversationId, limit).map { it.toDomain() }

    // ── Agent loop ────────────────────────────────────────────────────────

    override fun sendMessage(
        conversationId: String,
        content:        String,
        image:          String?,
        displayText:    String?,
    ): Flow<ChatEvent> = flow {

        // What actually gets persisted + shown in the user bubble. For
        // file-attachment turns this is the user's typed prompt only
        // (e.g. "Summarize this"); [content] carries the prepended
        // "[Attached document: foo.pdf] … --- Summarize this" block the
        // model needs as context.
        val persistedText = displayText ?: content

        // ── 1. Persist user message ───────────────────────────────────────
        val userContent = buildList {
            add(ContentBlockDto.Text(content))
            if (image != null) {
                add(ContentBlockDto.Image(
                    com.jarvis.android.data.api.dto.ImageSource("base64", "image/jpeg", image)
                ))
            }
        }
        val userEntity = MessageEntity(
            conversationId = conversationId,
            role           = "user",
            content        = persistedText,
            contentType    = if (image != null) "image" else "text",
            timestamp      = System.currentTimeMillis(),
        )
        messageDao.insert(userEntity)
        conversationDao.incrementStats(id = conversationId)

        // Auto-title: first 60 chars of the user's real prompt (never the
        // attached-document context block).
        conversationDao.getById(conversationId)?.let { conv ->
            if (conv.messageCount <= 1 && conv.title == NEW_CONVERSATION_TITLE) {
                conversationDao.updateTitle(conversationId, persistedText.take(60))
            }
        }

        // ── 2. Build API history ──────────────────────────────────────────
        val history = messageDao.getRecentByConversation(conversationId, limit = 40)
            .map { it.toMessageDto() }
            .toMutableList()

        // The user message we just inserted is already at the tail of history.
        // If it had an image, replace the last entry with the rich content version.
        if (image != null && history.isNotEmpty()) {
            history[history.lastIndex] = MessageDto(
                role    = "user",
                content = userContent,
            )
        }

        // ── 3. Route: Brain / Local / Direct cloud / Claude ─────────────────
        // Provider is decided by directProvider — the user picks once in
        // Settings (or per-message via the chat top-bar picker, which writes
        // through to directProvider too). connectionMode is no longer
        // consulted; one unified dropdown holds everything including Brain.
        if (apiKeyProviderImpl.directProvider == CloudProvider.JARVIS_BRAIN) {
            // Brain: stream from JARVIS homelab server, no local tool execution.
            val responseText = StringBuilder()
            brainApi.streamMessage(content).collect { chunk ->
                responseText.append(chunk)
                emit(ChatEvent.TextDelta(chunk))
            }
            val assistantEntity = MessageEntity(
                conversationId = conversationId,
                role           = "assistant",
                content        = responseText.toString(),
                contentType    = "text",
                timestamp      = System.currentTimeMillis(),
            )
            val id = messageDao.insert(assistantEntity)
            conversationDao.incrementStats(id = conversationId)
            emit(ChatEvent.TurnSaved(id))
            emit(ChatEvent.Done)
            return@flow
        }

        // Ask IntelliRouter which backend to use
        val decision = intelliRouter.route(content, hasImage = image != null)
        Log.i(TAG, "Routing: ${decision.reason} → ${decision.backend} model=${decision.localModelId}")

        if (decision.backend == Backend.LOCAL && decision.localModelId != null) {
            // ── LOCAL path ────────────────────────────────────────────────────
            val modelId = decision.localModelId

            // Load model if a different one is in memory (or nothing is loaded)
            if (modelRepository.observeLoadedModelId().value != modelId) {
                emit(ChatEvent.TextDelta(""))  // signal streaming started
                modelRepository.loadModel(modelId).collect { status ->
                    Log.d(TAG, "Load: $status")
                }
            }

            // Build ChatML prompt from conversation history
            val localPrompt = buildLocalPrompt(history, systemPrompt)

            // Multimodal backends (LiteRT-LM w/ Gemma 4 / Gemma 3n) can see
            // attached images. Decode the base64 the UI persisted on the user
            // turn into raw bytes so the backend can wrap them as
            // Content.ImageBytes. Non-multimodal backends ignore this list.
            val imageBytes: List<ByteArray> = if (image != null) {
                runCatching { listOf(android.util.Base64.decode(image, android.util.Base64.DEFAULT)) }
                    .onFailure { Log.w(TAG, "Failed to decode attached image bytes: ${it.message}") }
                    .getOrElse { emptyList() }
            } else emptyList()

            // Per-model tuning from the ModelConfigDialog (gear icon in the
            // chat top bar). Defaults fall through to Gemma/Qwen/DeepSeek's
            // recommended values when the user hasn't touched the sliders yet.
            val mc = apiKeyProviderImpl.getModelConfig(modelId)
            val genCfg = GenerationConfig(
                maxNewTokens   = mc.maxTokens,
                temperature    = mc.temperature,
                topK           = mc.topK,
                topP           = mc.topP,
                repeatPenalty  = 1.1f,   // not exposed in the Gallery-style dialog
                systemPrompt   = systemPrompt,
                images         = imageBytes,
                enableThinking = mc.enableThinking,
            )

            val responseBuilder = StringBuilder()
            modelRepository.generate(modelId, localPrompt, genCfg).collect { token ->
                responseBuilder.append(token)
                emit(ChatEvent.TextDelta(token))
            }

            val assistantEntity = MessageEntity(
                conversationId = conversationId,
                role           = "assistant",
                content        = responseBuilder.toString(),
                contentType    = "text",
                isOffline      = true,
                timestamp      = System.currentTimeMillis(),
            )
            val id = messageDao.insert(assistantEntity)
            conversationDao.incrementStats(id = conversationId)
            emit(ChatEvent.TurnSaved(id))
            emit(ChatEvent.Done)
            return@flow
        }

        // ── CLOUD path (default) ─────────────────────────────────────────────

        // Direct multi-provider (OpenAI-compat) — now with full agent loop.
        // Streams responses, accumulates tool_calls deltas, executes each via
        // JarvisToolDispatcher, appends results as role:"tool" messages, and
        // re-streams until the model returns finish_reason = "stop".
        var directProvider = apiKeyProviderImpl.directProvider

        // Vision auto-route: if the user attached an image but the active
        // model can't see images (Groq, DeepSeek, …) swap to a
        // vision-capable model from any other provider whose key is
        // configured. Only for this turn — the user's normal selection is
        // preserved. Falls through untouched if no vision-capable key is
        // available, in which case the model will just politely deny
        // seeing it.
        if (image != null) {
            val currentSupports = CloudModel.CATALOG.firstOrNull {
                it.provider == directProvider &&
                it.id       == apiKeyProviderImpl.getDirectModel(directProvider)
            }?.supportsVision == true
            if (!currentSupports) {
                val fallback = pickVisionCapableFallback(
                    currentProvider = directProvider,
                    apiKeyProvider  = apiKeyProviderImpl,
                )
                if (fallback != null) {
                    Log.i(TAG, "Vision auto-route: $directProvider → ${fallback.provider}/${fallback.id}")
                    emit(ChatEvent.Warning(
                        "Image sent to ${fallback.label} — your current model (${directProvider.displayName}) doesn't support images."
                    ))
                    directProvider = fallback.provider
                    apiKeyProviderImpl.saveDirectModel(fallback.provider, fallback.id)
                } else {
                    emit(ChatEvent.Warning(
                        "${directProvider.displayName} can't see images. Add an OpenAI/Anthropic/Google/Mistral/xAI/OpenRouter key in Settings to enable vision."
                    ))
                }
            }
        }

        if (directProvider != CloudProvider.ANTHROPIC &&
            directProvider != CloudProvider.GOOGLE &&
            directProvider != CloudProvider.JARVIS_BRAIN) {

            val stored = apiKeyProviderImpl.getDirectModel(directProvider)
            val model = stored.ifBlank {
                CloudModel.CATALOG.firstOrNull { it.provider == directProvider }?.id
                    ?: error("No catalog model for ${directProvider.displayName}")
            }
            val key = apiKeyProviderImpl.getProviderKey(directProvider)
            val toolDefs = ToolDefinitions.ALL
            val workingHistory = history.toMutableList()
            var iter = 0
            var finalText = ""
            var ended = false

            while (iter < MAX_TOOL_ITERATIONS && !ended) {
                iter++
                // Partial-tool-call merge buckets, keyed by `index`.
                data class ToolBuilder(
                    var id:    String? = null,
                    var name:  String? = null,
                    val args:  StringBuilder = StringBuilder(),
                )
                val toolBuilders = mutableMapOf<Int, ToolBuilder>()
                val textBuilder  = StringBuilder()
                var finish: String? = null
                var streamErr: String? = null

                try {
                    openAiCompatApi.streamChat(
                        provider = directProvider,
                        apiKey   = key,
                        model    = model,
                        system   = systemPrompt,
                        messages = workingHistory,
                        tools    = toolDefs,
                    ).collect { ev ->
                        when (ev) {
                            is com.jarvis.android.data.api.OpenAiStreamEvent.Text -> {
                                textBuilder.append(ev.delta)
                                emit(ChatEvent.TextDelta(ev.delta))
                            }
                            is com.jarvis.android.data.api.OpenAiStreamEvent.ToolCallDelta -> {
                                val b = toolBuilders.getOrPut(ev.index) { ToolBuilder() }
                                if (ev.id   != null) b.id   = ev.id
                                if (ev.name != null) b.name = ev.name
                                if (ev.argsDelta != null) b.args.append(ev.argsDelta)
                            }
                            is com.jarvis.android.data.api.OpenAiStreamEvent.Done -> {
                                finish = ev.finishReason
                            }
                            is com.jarvis.android.data.api.OpenAiStreamEvent.Error -> {
                                streamErr = ev.message
                            }
                        }
                    }
                } catch (e: Exception) {
                    streamErr = e.message
                }

                if (streamErr != null) {
                    Log.e(TAG, "Direct cloud (${directProvider.name}) stream failed: $streamErr")
                    emit(ChatEvent.TextDelta("\n[${directProvider.displayName} error: $streamErr]"))
                    ended = true
                    break
                }

                finalText = textBuilder.toString()

                // Build the ToolUseBlocks the agent loop expects. Skip any
                // partial that never got a name (unusable).
                val toolUses = toolBuilders.entries
                    .sortedBy { it.key }
                    .mapNotNull { (_, b) ->
                        val name = b.name ?: return@mapNotNull null
                        val id   = b.id ?: "call_${System.nanoTime()}"
                        val argsText = b.args.toString().ifBlank { "{}" }
                        val argsJson = runCatching {
                            kotlinx.serialization.json.Json
                                .parseToJsonElement(argsText)
                                .let { it as kotlinx.serialization.json.JsonObject }
                        }.getOrElse { kotlinx.serialization.json.JsonObject(emptyMap()) }
                        com.jarvis.android.system.tools.ToolUseBlock(id, name, argsJson)
                    }

                if (toolUses.isEmpty() || finish != "tool_calls") {
                    ended = true
                    break
                }

                // Append assistant turn with tool_calls to history so the next
                // streamChat() sees it in role:"assistant" shape.
                val asstContent = mutableListOf<ContentBlockDto>()
                if (finalText.isNotBlank()) asstContent += ContentBlockDto.Text(finalText)
                toolUses.forEach { asstContent += ContentBlockDto.ToolUse(it.id, it.name, it.input) }
                workingHistory += MessageDto(role = "assistant", content = asstContent)

                // Execute tools.
                val results = toolUses.map { toolUse ->
                    emit(ChatEvent.ToolCallStarted(toolUse.id, toolUse.name))
                    val r = toolDispatcher.dispatch(toolUse)
                    emit(ChatEvent.ToolCallCompleted(
                        toolId   = toolUse.id,
                        toolName = toolUse.name,
                        result   = r.content.take(200),
                        isError  = r.isError,
                    ))
                    r
                }
                // Bundle tool results as a user message for translation in
                // OpenAiCompatApiService.messagesToJson — each ToolResult
                // block fans out to its own role:"tool" entry there.
                workingHistory += MessageDto(
                    role    = "user",
                    content = results.map { tr ->
                        ContentBlockDto.ToolResult(
                            toolUseId = tr.toolUseId,
                            content   = tr.content,
                            isError   = tr.isError,
                        )
                    },
                )
            }

            val assistantEntity = MessageEntity(
                conversationId = conversationId,
                role           = "assistant",
                content        = finalText,
                contentType    = "text",
                timestamp      = System.currentTimeMillis(),
            )
            val id = messageDao.insert(assistantEntity)
            conversationDao.incrementStats(id = conversationId)
            emit(ChatEvent.TurnSaved(id))
            emit(ChatEvent.Done)
            return@flow
        }

        var iteration = 0
        var lastAssistantMsgId: Long = -1

        while (iteration < MAX_TOOL_ITERATIONS) {
            val accumulator = claudeApi.StreamAccumulator()
            var streamError: String? = null

            claudeApi.streamMessage(
                messages  = history,
                system    = systemPrompt,
                tools     = ToolDefinitions.ALL,
            ).collect { event ->
                accumulator.feed(event)
                when (event) {
                    is SseStreamEvent.ContentBlockDelta -> {
                        val d = event.delta
                        if (d is ContentDelta.TextDelta && d.text.isNotEmpty()) {
                            emit(ChatEvent.TextDelta(d.text))
                        }
                    }
                    is SseStreamEvent.StreamError -> {
                        streamError = event.error.message
                    }
                    else -> Unit
                }
            }

            if (streamError != null) {
                emit(ChatEvent.Error(streamError, isRetryable = true))
                return@flow
            }

            val turn: AssistantTurn = accumulator.build()

            // ── 4. Persist assistant turn ─────────────────────────────────
            val toolCallsJson = if (turn.toolUseBlocks.isNotEmpty()) {
                json.encodeToString(turn.toolUseBlocks)
            } else null

            val assistantEntity = MessageEntity(
                conversationId = conversationId,
                role           = "assistant",
                content        = turn.fullText,
                contentType    = when {
                    turn.hasToolUse && turn.fullText.isNotBlank() -> "mixed"
                    turn.hasToolUse                               -> "tool_use"
                    else                                          -> "text"
                },
                toolCallsJson  = toolCallsJson,
                inputTokens    = turn.inputTokens,
                outputTokens   = turn.outputTokens,
                stopReason     = turn.stopReason,
                timestamp      = System.currentTimeMillis(),
            )
            lastAssistantMsgId = messageDao.insert(assistantEntity)
            conversationDao.incrementStats(
                id           = conversationId,
                inputTokens  = turn.inputTokens,
                outputTokens = turn.outputTokens,
            )

            // ── 5. If no tool use, we're done ─────────────────────────────
            if (!turn.hasToolUse || turn.stopReason == "end_turn") break

            // ── 6. Execute tools ──────────────────────────────────────────
            val toolResults = turn.toolUseBlocks.map { toolUse ->
                emit(ChatEvent.ToolCallStarted(toolUse.id, toolUse.name))

                val result = toolDispatcher.dispatch(toolUse)

                emit(ChatEvent.ToolCallCompleted(
                    toolId   = toolUse.id,
                    toolName = toolUse.name,
                    result   = result.content.take(200),
                    isError  = result.isError,
                ))
                result
            }

            // Persist tool results as a user message
            val toolResultJson = json.encodeToString(toolResults)
            val toolResultEntity = MessageEntity(
                conversationId = conversationId,
                role           = "user",
                content        = "[tool results]",
                contentType    = "tool_result",
                toolCallsJson  = toolResultJson,
                timestamp      = System.currentTimeMillis(),
            )
            messageDao.insert(toolResultEntity)
            conversationDao.incrementStats(id = conversationId)

            // Extend history for next loop iteration
            history.add(claudeApi.assistantMessage(turn.textBlocks, turn.toolUseBlocks))
            history.add(claudeApi.toolResultMessage(toolResults))

            iteration++
        }

        if (iteration >= MAX_TOOL_ITERATIONS) {
            emit(ChatEvent.Warning("Max tool iterations ($MAX_TOOL_ITERATIONS) reached"))
        }

        if (lastAssistantMsgId > 0) {
            emit(ChatEvent.TurnSaved(lastAssistantMsgId))
        }
        emit(ChatEvent.Done)

    }.catch { e ->
        Log.e(TAG, "sendMessage error", e)
        emit(ChatEvent.Error(e.message ?: "Unknown error", isRetryable = true))
    }

    // ── Mapping helpers ───────────────────────────────────────────────────

    private fun ConversationEntity.toDomain() = Conversation(
        id                = id,
        title             = title,
        model             = model,
        createdAt         = createdAt,
        updatedAt         = updatedAt,
        messageCount      = messageCount,
        totalInputTokens  = totalInputTokens,
        totalOutputTokens = totalOutputTokens,
        isPinned          = isPinned,
    )

    private fun MessageEntity.toDomain() = Message(
        id             = id,
        conversationId = conversationId,
        role           = MessageRole.from(role),
        content        = content,
        contentType    = when (contentType) {
            "tool_use"    -> MessageContentType.TOOL_USE
            "tool_result" -> MessageContentType.TOOL_RESULT
            "image"       -> MessageContentType.IMAGE
            "mixed"       -> MessageContentType.MIXED
            else          -> MessageContentType.TEXT
        },
        toolCallsJson  = toolCallsJson,
        timestamp      = timestamp,
        inputTokens    = inputTokens,
        outputTokens   = outputTokens,
        stopReason     = stopReason,
        isOffline      = isOffline,
    )

    private fun MessageEntity.toMessageDto(): MessageDto {
        val contentBlocks: List<ContentBlockDto> = when (contentType) {
            "tool_result" -> {
                try {
                    json.decodeFromString<List<com.jarvis.android.system.tools.ToolResultBlock>>(
                        toolCallsJson ?: "[]"
                    ).map { r ->
                        ContentBlockDto.ToolResult(
                            toolUseId = r.toolUseId,
                            content   = r.content,
                            isError   = r.isError,
                        )
                    }
                } catch (_: Exception) { listOf(ContentBlockDto.Text(content)) }
            }
            "tool_use", "mixed" -> {
                val textBlocks = if (content.isNotBlank()) listOf(ContentBlockDto.Text(content)) else emptyList()
                val toolBlocks = try {
                    json.decodeFromString<List<com.jarvis.android.system.tools.ToolUseBlock>>(
                        toolCallsJson ?: "[]"
                    ).map { t ->
                        ContentBlockDto.ToolUse(id = t.id, name = t.name, input = t.input)
                    }
                } catch (_: Exception) { emptyList() }
                textBlocks + toolBlocks
            }
            else -> listOf(ContentBlockDto.Text(content))
        }
        return MessageDto(role = role, content = contentBlocks)
    }

    /**
     * Converts the conversation [history] to a ChatML-formatted prompt string.
     * ChatML is natively understood by Qwen, TinyLlama, Phi-3, and most modern
     * instruction-tuned GGUF models.
     *
     * Format:
     *   <|im_start|>system\n{system}\n<|im_end|>\n
     *   <|im_start|>user\n{msg}\n<|im_end|>\n
     *   <|im_start|>assistant\n{msg}\n<|im_end|>\n
     *   ...
     *   <|im_start|>assistant\n          ← trailing prompt for the model to continue
     */
    private fun buildLocalPrompt(
        history: List<MessageDto>,
        system:  String,
    ): String = buildString {
        if (system.isNotBlank()) {
            append("<|im_start|>system\n")
            append(system)
            append("\n<|im_end|>\n")
        }
        for (msg in history) {
            val text = msg.content
                .filterIsInstance<ContentBlockDto.Text>()
                .joinToString("") { it.text }
                .trim()
            if (text.isNotBlank()) {
                append("<|im_start|>${msg.role}\n")
                append(text)
                append("\n<|im_end|>\n")
            }
        }
        append("<|im_start|>assistant\n")
    }

    private companion object {
        const val TAG                   = "ChatRepositoryImpl"
        const val MAX_TOOL_ITERATIONS   = 10
        const val NEW_CONVERSATION_TITLE = "New conversation"
    }
}

/**
 * Return the best vision-capable [CloudModel] from any provider (other than
 * [currentProvider]) whose API key is configured, or null if the user
 * hasn't set up any vision-capable provider yet.
 *
 * Priority: whatever slug the user most recently picked on that provider
 * (if vision-capable), else the catalog's first vision-capable entry.
 * Ordered by provider preference: OpenAI → Anthropic → Google → Mistral
 * → xAI → OpenRouter. Anthropic/Google are handled by their own code
 * paths in the repo, so we prefer them here too — the top-level router
 * will pick them up naturally once [directProvider] is swapped.
 */
private fun pickVisionCapableFallback(
    currentProvider: CloudProvider,
    apiKeyProvider:  com.jarvis.android.data.repository.ApiKeyProviderImpl,
): CloudModel? {
    val order = listOf(
        CloudProvider.OPENAI,
        CloudProvider.ANTHROPIC,
        CloudProvider.GOOGLE,
        CloudProvider.MISTRAL,
        CloudProvider.XAI,
        CloudProvider.OPENROUTER,
    )
    for (p in order) {
        if (p == currentProvider) continue
        if (!apiKeyProvider.hasApiKey(p)) continue
        val userPicked = apiKeyProvider.getDirectModel(p)
        val picked = CloudModel.CATALOG.firstOrNull {
            it.provider == p && it.id == userPicked && it.supportsVision
        } ?: CloudModel.CATALOG.firstOrNull {
            it.provider == p && it.supportsVision
        }
        if (picked != null) return picked
    }
    return null
}
