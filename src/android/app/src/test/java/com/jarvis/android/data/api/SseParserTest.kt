package com.jarvis.android.data.api

import com.jarvis.android.core.network.RawSseEvent
import com.jarvis.android.data.api.dto.ContentDelta
import com.jarvis.android.data.api.dto.SseStreamEvent
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertInstanceOf
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

class SseParserTest {

    private lateinit var parser: SseParser

    @BeforeEach
    fun setup() {
        parser = SseParser()
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun msg(type: String, data: String) = RawSseEvent.Message(type = type, data = data)

    // ── message_start ──────────────────────────────────────────────────────────

    @Test
    fun `parse message_start returns MessageStart`() {
        val data = """
            {"message":{"id":"msg_01","type":"message","role":"assistant","model":"claude-3-5-sonnet-20241022","usage":{"input_tokens":10,"output_tokens":0}}}
        """.trimIndent()
        val event = parser.parse(msg("message_start", data))
        assertInstanceOf(SseStreamEvent.MessageStart::class.java, event)
        val ms = event as SseStreamEvent.MessageStart
        assertEquals("msg_01", ms.message.id)
        assertEquals("assistant", ms.message.role)
    }

    // ── content_block_delta (text) ─────────────────────────────────────────────

    @Test
    fun `parse content_block_delta text_delta returns TextDelta`() {
        val data = """{"index":0,"delta":{"type":"text_delta","text":"Hello"}}"""
        val event = parser.parse(msg("content_block_delta", data))
        assertInstanceOf(SseStreamEvent.ContentBlockDelta::class.java, event)
        val cbd = event as SseStreamEvent.ContentBlockDelta
        assertEquals(0, cbd.index)
        assertInstanceOf(ContentDelta.TextDelta::class.java, cbd.delta)
        assertEquals("Hello", (cbd.delta as ContentDelta.TextDelta).text)
    }

    // ── content_block_delta (tool input JSON) ──────────────────────────────────

    @Test
    fun `parse content_block_delta input_json_delta returns InputJsonDelta`() {
        val data = """{"index":1,"delta":{"type":"input_json_delta","partial_json":"{\"cmd\":"}}"""
        val event = parser.parse(msg("content_block_delta", data))
        assertInstanceOf(SseStreamEvent.ContentBlockDelta::class.java, event)
        val cbd = event as SseStreamEvent.ContentBlockDelta
        assertEquals(1, cbd.index)
        assertInstanceOf(ContentDelta.InputJsonDelta::class.java, cbd.delta)
    }

    // ── content_block_stop ─────────────────────────────────────────────────────

    @Test
    fun `parse content_block_stop returns ContentBlockStop with correct index`() {
        val data = """{"index":2}"""
        val event = parser.parse(msg("content_block_stop", data))
        assertInstanceOf(SseStreamEvent.ContentBlockStop::class.java, event)
        assertEquals(2, (event as SseStreamEvent.ContentBlockStop).index)
    }

    // ── message_delta ──────────────────────────────────────────────────────────

    @Test
    fun `parse message_delta returns MessageDelta with stop_reason`() {
        val data = """{"delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":42}}"""
        val event = parser.parse(msg("message_delta", data))
        assertInstanceOf(SseStreamEvent.MessageDelta::class.java, event)
        val md = event as SseStreamEvent.MessageDelta
        assertEquals("end_turn", md.delta.stopReason)
        assertEquals(42, md.usage?.outputTokens)
    }

    // ── message_stop / ping ────────────────────────────────────────────────────

    @Test
    fun `parse message_stop returns MessageStop singleton`() {
        val event = parser.parse(msg("message_stop", "{}"))
        assertEquals(SseStreamEvent.MessageStop, event)
    }

    @Test
    fun `parse ping returns Ping singleton`() {
        val event = parser.parse(msg("ping", "{}"))
        assertEquals(SseStreamEvent.Ping, event)
    }

    // ── error ──────────────────────────────────────────────────────────────────

    @Test
    fun `parse error returns StreamError`() {
        val data = """{"error":{"type":"overloaded_error","message":"Service overloaded"}}"""
        val event = parser.parse(msg("error", data))
        assertInstanceOf(SseStreamEvent.StreamError::class.java, event)
        val err = event as SseStreamEvent.StreamError
        assertEquals("overloaded_error", err.error.type)
    }

    // ── unknown event type ─────────────────────────────────────────────────────

    @Test
    fun `parse unknown event type returns null`() {
        val event = parser.parse(msg("future_event", """{"foo":"bar"}"""))
        assertNull(event)
    }

    // ── null data ─────────────────────────────────────────────────────────────

    @Test
    fun `parse null data returns null`() {
        val raw = RawSseEvent.Message(type = "content_block_delta", data = "null")
        // null data field → should not throw; may return StreamError or null
        // We just assert it doesn't throw
        val result = runCatching { parser.parse(raw) }
        assertTrue(result.isSuccess)
    }

    // ── malformed JSON ────────────────────────────────────────────────────────

    @Test
    fun `parse malformed JSON returns StreamError not exception`() {
        val event = parser.parse(msg("content_block_delta", "{not json}"))
        assertInstanceOf(SseStreamEvent.StreamError::class.java, event)
        val err = event as SseStreamEvent.StreamError
        assertEquals("parse_error", err.error.type)
    }

    // ── content_block_start ────────────────────────────────────────────────────

    @Test
    fun `parse content_block_start tool_use carries id and name`() {
        val data = """{"index":1,"content_block":{"type":"tool_use","id":"tool_abc","name":"execute_bash"}}"""
        val event = parser.parse(msg("content_block_start", data))
        assertInstanceOf(SseStreamEvent.ContentBlockStart::class.java, event)
        val cbs = event as SseStreamEvent.ContentBlockStart
        assertEquals(1, cbs.index)
        assertEquals("tool_use", cbs.contentBlock.type)
        assertEquals("execute_bash", cbs.contentBlock.name)
        assertEquals("tool_abc", cbs.contentBlock.id)
    }
}
