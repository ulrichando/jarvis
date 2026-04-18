package com.jarvis.android.core.network

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.channels.trySendBlocking
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Low-level OkHttp SSE → [Flow] bridge.
 *
 * Converts an OkHttp [Request] into a cold [Flow] of [RawSseEvent]s using
 * OkHttp's EventSource API backed by a [callbackFlow].
 *
 * Lifecycle:
 *   - Flow is cold — no connection is opened until `collect {}` starts.
 *   - Cancelling the collecting coroutine cancels the OkHttp call via [awaitClose].
 *   - The flow completes normally on [RawSseEvent.Complete].
 *   - The flow completes with an exception on [RawSseEvent.Failure].
 *
 * Usage (from a repository):
 *   sseClient.stream(request).collect { event ->
 *       when (event) {
 *           is RawSseEvent.Message  -> sseParser.parse(event.type, event.data)
 *           is RawSseEvent.Complete -> // stream ended normally
 *           is RawSseEvent.Failure  -> throw event.t ?: IOException(event.response?.message)
 *       }
 *   }
 *
 * Thread safety: [EventSourceListener] callbacks arrive on OkHttp's thread pool.
 * [trySendBlocking] is safe to call from non-coroutine threads.
 */
@Singleton
class SseClient @Inject constructor(
    private val okHttpClient: OkHttpClient,
) {

    /**
     * Opens an SSE connection for [request] and emits raw events.
     *
     * The returned [Flow] is cold and cancellable. It will:
     *   - emit [RawSseEvent.Message] for each `data:` line with an optional `event:` type
     *   - emit [RawSseEvent.Complete] when the server closes the stream cleanly
     *   - emit [RawSseEvent.Failure] on HTTP error or network failure, then complete
     */
    fun stream(request: Request): Flow<RawSseEvent> = callbackFlow {
        val listener = object : EventSourceListener() {

            override fun onEvent(
                eventSource: EventSource,
                id: String?,
                type: String?,
                data: String,
            ) {
                // trySendBlocking suspends the OkHttp thread until the channel has space,
                // providing back-pressure without dropping events.
                trySendBlocking(RawSseEvent.Message(type = type, data = data, id = id))
            }

            override fun onClosed(eventSource: EventSource) {
                trySendBlocking(RawSseEvent.Complete)
                close() // close the Flow normally
            }

            override fun onFailure(
                eventSource: EventSource,
                t: Throwable?,
                response: Response?,
            ) {
                val event = RawSseEvent.Failure(
                    t        = t,
                    code     = response?.code,
                    message  = response?.message,
                )
                trySendBlocking(event)
                // Close with the exception so the collector can react.
                // If t is null (HTTP error), wrap in an IOException.
                val cause = t ?: java.io.IOException(
                    "SSE connection failed: HTTP ${response?.code} ${response?.message}"
                )
                close(cause)
            }
        }

        val eventSource = EventSources.createFactory(okHttpClient)
            .newEventSource(request, listener)

        // When the coroutine is cancelled (user taps Stop, ViewModel clears, etc.),
        // cancel the OkHttp call. This sends TCP FIN to the server and stops
        // any further callbacks from the EventSourceListener.
        awaitClose {
            eventSource.cancel()
        }
    }
}

// ── Raw SSE event types ───────────────────────────────────────────────────────

/**
 * Raw events emitted by [SseClient.stream] before parsing.
 * The data layer's SseParser converts these into typed domain events.
 */
sealed interface RawSseEvent {

    /**
     * A standard SSE message line received from the server.
     * @param type  The `event:` field value ("content_block_delta", "message_stop", etc.).
     *              Null if the server omitted the event field.
     * @param data  The `data:` field payload (JSON string for the Claude API).
     * @param id    The `id:` field, if present (not used by Claude API).
     */
    data class Message(
        val type: String?,
        val data: String,
        val id: String?   = null,
    ) : RawSseEvent

    /**
     * The server closed the SSE stream cleanly (no error).
     * Corresponds to [EventSourceListener.onClosed].
     */
    data object Complete : RawSseEvent

    /**
     * The connection failed — either a network error or an HTTP error response.
     * @param t       The underlying exception, or null for HTTP-level errors.
     * @param code    HTTP status code if available.
     * @param message HTTP status message if available.
     */
    data class Failure(
        val t: Throwable?,
        val code: Int?    = null,
        val message: String? = null,
    ) : RawSseEvent
}
