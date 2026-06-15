package com.jarvis.android.data.repository

import android.content.Context
import android.content.res.AssetManager
import app.cash.turbine.test
import com.jarvis.android.data.api.BrainApiService
import com.jarvis.android.data.api.ClaudeApiService
import com.jarvis.android.data.api.OpenAiCompatApiService
import com.jarvis.android.data.api.dto.ContentBlockStartData
import com.jarvis.android.data.api.dto.ContentDelta
import com.jarvis.android.data.api.dto.DeltaUsage
import com.jarvis.android.data.api.dto.MessageDeltaData
import com.jarvis.android.data.api.dto.MessageStartData
import com.jarvis.android.data.api.dto.SseStreamEvent
import com.jarvis.android.data.api.dto.TokenUsage
import com.jarvis.android.data.local.dao.ConversationDao
import com.jarvis.android.data.local.dao.MessageDao
import com.jarvis.android.data.local.entity.ConversationEntity
import com.jarvis.android.domain.model.ChatEvent
import com.jarvis.android.domain.model.CloudProvider
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.system.llm.Backend
import com.jarvis.android.system.llm.IntelliRouter
import com.jarvis.android.system.llm.RoutingDecision
import com.jarvis.android.system.tools.JarvisToolDispatcher
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertInstanceOf
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import java.io.ByteArrayInputStream

/**
 * Unit tests for the [ChatRepositoryImpl] agent loop.
 *
 * Strategy:
 *   - Mock all I/O (DAOs, API, context assets).
 *   - Provide real SSE event sequences to `claudeApi.streamMessage` so the
 *     actual `StreamAccumulator` (inner class) processes them without needing
 *     to mock its constructor.
 *   - Tests focus on text-only turns and error turns; tool-use dispatch logic
 *     is covered separately in [JarvisToolDispatcherTest].
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatRepositoryImplTest {

    // ── Mocks ─────────────────────────────────────────────────────────────────

    private val context         = mockk<Context>(relaxed = true)
    private val assetManager    = mockk<AssetManager>(relaxed = true)
    private val conversationDao = mockk<ConversationDao>(relaxed = true)
    private val messageDao      = mockk<MessageDao>(relaxed = true)
    private val claudeApi       = mockk<ClaudeApiService>(relaxed = true)
    private val brainApi        = mockk<BrainApiService>(relaxed = true)
    private val openAiCompatApi = mockk<OpenAiCompatApiService>(relaxed = true)
    private val apiKeyProvider  = mockk<ApiKeyProviderImpl>(relaxed = true)
    private val toolDispatcher  = mockk<JarvisToolDispatcher>(relaxed = true)
    private val intelliRouter   = mockk<IntelliRouter>(relaxed = true)
    private val modelRepository = mockk<ModelRepository>(relaxed = true)

    private lateinit var repo: ChatRepositoryImpl

    private val conversationId = "conv-001"
    private val fakeConv = ConversationEntity(
        id           = conversationId,
        title        = "New conversation",
        model        = "claude-sonnet-4-6",
        createdAt    = 1000L,
        updatedAt    = 1000L,
        messageCount = 0,
    )

    @BeforeEach
    fun setup() {
        every { context.assets } returns assetManager
        every { assetManager.open("jarvis_persona.txt") } returns
            ByteArrayInputStream("You are JARVIS.".toByteArray())

        coEvery { conversationDao.getById(any()) }     returns fakeConv
        coEvery { conversationDao.insert(any()) }      returns Unit
        coEvery { conversationDao.incrementStats(any()) } returns Unit
        coEvery { conversationDao.updateTitle(any(), any()) } returns Unit

        coEvery { messageDao.insert(any()) }                            returns 1L
        coEvery { messageDao.getRecentByConversation(any(), any()) }    returns emptyList()

        // Route every turn to the cloud/Anthropic path so the stubbed
        // claudeApi.streamMessage SSE flows in each test are actually consumed.
        coEvery { intelliRouter.route(any(), any()) } returns
            RoutingDecision(backend = Backend.CLOUD, reason = "test")
        every { apiKeyProvider.directProvider } returns CloudProvider.ANTHROPIC

        repo = ChatRepositoryImpl(
            context,
            conversationDao,
            messageDao,
            claudeApi,
            brainApi,
            openAiCompatApi,
            apiKeyProvider,
            toolDispatcher,
            intelliRouter,
            modelRepository
        )
    }

    // ── SSE event helpers ─────────────────────────────────────────────────────

    /** Minimal text-only SSE sequence that the real StreamAccumulator will handle. */
    private fun textSseFlow(vararg chunks: String) = flow<SseStreamEvent> {
        emit(SseStreamEvent.MessageStart(
            MessageStartData(
                id    = "msg_test",
                type  = "message",
                role  = "assistant",
                model = "claude-sonnet-4-6",
                usage = TokenUsage(inputTokens = 10, outputTokens = 0),
            )
        ))
        emit(SseStreamEvent.ContentBlockStart(
            index        = 0,
            contentBlock = ContentBlockStartData(type = "text"),
        ))
        chunks.forEach { chunk ->
            emit(SseStreamEvent.ContentBlockDelta(0, ContentDelta.TextDelta(chunk)))
        }
        emit(SseStreamEvent.ContentBlockStop(0))
        emit(SseStreamEvent.MessageDelta(
            delta = MessageDeltaData(stopReason = "end_turn", stopSequence = null),
            usage = DeltaUsage(outputTokens = chunks.size),
        ))
        emit(SseStreamEvent.MessageStop)
    }

    /** SSE flow that terminates with a server error. */
    private fun errorSseFlow(message: String) = flow<SseStreamEvent> {
        emit(SseStreamEvent.StreamError(
            com.jarvis.android.data.api.dto.ApiError(
                type    = "overloaded_error",
                message = message,
            )
        ))
    }

    // ── Text-only turn ────────────────────────────────────────────────────────

    @Test
    fun `text-only turn emits TextDelta then TurnSaved then Done`() = runTest {
        every { claudeApi.streamMessage(any(), any(), any(), any(), any()) } returns
            textSseFlow("Hello", " world", "!")

        repo.sendMessage(conversationId, "Hi").test {
            val emitted = mutableListOf<ChatEvent>()

            var event = awaitItem()
            while (event !is ChatEvent.Done) {
                emitted.add(event)
                event = awaitItem()
            }
            emitted.add(event)

            // All three text chunks should arrive
            val deltas = emitted.filterIsInstance<ChatEvent.TextDelta>()
            assertEquals(3, deltas.size)
            assertEquals("Hello world!", deltas.joinToString("") { it.text })

            // TurnSaved must precede Done
            val savedIdx = emitted.indexOfFirst { it is ChatEvent.TurnSaved }
            val doneIdx  = emitted.indexOfLast  { it is ChatEvent.Done }
            assertTrue(savedIdx >= 0, "TurnSaved should be emitted")
            assertTrue(doneIdx  > savedIdx, "Done should come after TurnSaved")

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `empty streamed text produces TurnSaved and Done without TextDelta`() = runTest {
        every { claudeApi.streamMessage(any(), any(), any(), any(), any()) } returns
            textSseFlow()  // no text chunks

        repo.sendMessage(conversationId, "Hi").test {
            val emitted = mutableListOf<ChatEvent>()
            var event = awaitItem()
            while (event !is ChatEvent.Done) {
                emitted.add(event)
                event = awaitItem()
            }
            emitted.add(event)

            val deltas = emitted.filterIsInstance<ChatEvent.TextDelta>()
            assertTrue(deltas.isEmpty(), "No TextDelta when stream had no text")
            assertTrue(emitted.any { it is ChatEvent.TurnSaved })
            assertInstanceOf(ChatEvent.Done::class.java, emitted.last())

            cancelAndIgnoreRemainingEvents()
        }
    }

    // ── Error turn ────────────────────────────────────────────────────────────

    @Test
    fun `stream error emits ChatEvent Error with retryable=true`() = runTest {
        every { claudeApi.streamMessage(any(), any(), any(), any(), any()) } returns
            errorSseFlow("Service temporarily overloaded")

        repo.sendMessage(conversationId, "Hi").test {
            val event = awaitItem()
            assertInstanceOf(ChatEvent.Error::class.java, event)
            val err = event as ChatEvent.Error
            assertEquals("Service temporarily overloaded", err.message)
            assertTrue(err.isRetryable, "SSE stream errors should be retryable")
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ── Conversation CRUD ─────────────────────────────────────────────────────

    @Test
    fun `createConversation inserts entity and returns domain model`() = runTest {
        coEvery { conversationDao.insert(any()) } returns Unit
        coEvery { conversationDao.getById(any()) } returns fakeConv

        val conv = repo.createConversation("Test", "claude-sonnet-4-6")
        assertEquals("claude-sonnet-4-6", conv.model)
        assertFalse(conv.id.isBlank(), "Conversation id should be auto-generated UUID")
    }

    @Test
    fun `getConversation returns null when DAO returns null`() = runTest {
        coEvery { conversationDao.getById("missing") } returns null
        val result = repo.getConversation("missing")
        assertEquals(null, result)
    }

    @Test
    fun `deleteConversation delegates to DAO`() = runTest {
        coEvery { conversationDao.deleteById("conv-001") } returns Unit
        repo.deleteConversation("conv-001")
        io.mockk.coVerify(exactly = 1) { conversationDao.deleteById("conv-001") }
    }

    // ── Auto-title logic ──────────────────────────────────────────────────────

    @Test
    fun `sendMessage sets conversation title from first user message`() = runTest {
        val untitledConv = fakeConv.copy(messageCount = 0, title = "New conversation")
        coEvery { conversationDao.getById(any()) } returns untitledConv
        every { claudeApi.streamMessage(any(), any(), any(), any(), any()) } returns
            textSseFlow("Sure thing!")

        repo.sendMessage(conversationId, "What's the weather?").test {
            // Drain to completion
            while (awaitItem() !is ChatEvent.Done) { /* consume */ }
            cancelAndIgnoreRemainingEvents()
        }

        io.mockk.coVerify {
            conversationDao.updateTitle(
                conversationId,
                "What's the weather?",
            )
        }
    }
}
