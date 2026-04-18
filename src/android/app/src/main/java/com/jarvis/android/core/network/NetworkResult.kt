package com.jarvis.android.core.network

/**
 * Generic result wrapper for all network and repository operations.
 *
 * Use cases return `Flow<NetworkResult<T>>` or `Result<T>`.
 * ViewModels map these to sealed UiState variants.
 *
 * Usage:
 *   when (val result = fetchSomething()) {
 *       is NetworkResult.Success -> render(result.data)
 *       is NetworkResult.Error   -> showError(result.message)
 *       is NetworkResult.Loading -> showSpinner()
 *   }
 */
sealed class NetworkResult<out T> {

    /** Operation succeeded. [data] holds the response. */
    data class Success<out T>(val data: T) : NetworkResult<T>()

    /**
     * Operation failed.
     * @param code    HTTP status code, or null for non-HTTP errors (timeout, no network, parse).
     * @param message Human-readable error string — safe to show in UI (never contains API key).
     * @param cause   Original throwable for logging/debugging.
     */
    data class Error(
        val code: Int?          = null,
        val message: String,
        val cause: Throwable?   = null,
    ) : NetworkResult<Nothing>()

    /** Operation in progress. */
    data object Loading : NetworkResult<Nothing>()
}

// ── Extension helpers ─────────────────────────────────────────────────────────

/** True when this result is [NetworkResult.Success]. */
val <T> NetworkResult<T>.isSuccess: Boolean
    get() = this is NetworkResult.Success

/** True when this result is [NetworkResult.Error]. */
val <T> NetworkResult<T>.isError: Boolean
    get() = this is NetworkResult.Error

/** True when this result is [NetworkResult.Loading]. */
val <T> NetworkResult<T>.isLoading: Boolean
    get() = this is NetworkResult.Loading

/** Returns the data or null if not a [NetworkResult.Success]. */
fun <T> NetworkResult<T>.dataOrNull(): T? =
    (this as? NetworkResult.Success)?.data

/** Returns the error message or null if not a [NetworkResult.Error]. */
fun <T> NetworkResult<T>.errorMessageOrNull(): String? =
    (this as? NetworkResult.Error)?.message

/**
 * Maps a successful result's data, leaving Loading and Error untouched.
 *
 * Usage:
 *   val mapped: NetworkResult<String> = result.map { it.name }
 */
fun <T, R> NetworkResult<T>.map(transform: (T) -> R): NetworkResult<R> =
    when (this) {
        is NetworkResult.Success -> NetworkResult.Success(transform(data))
        is NetworkResult.Error   -> this
        is NetworkResult.Loading -> NetworkResult.Loading
    }

/**
 * Wraps a suspending [block] in a try/catch and returns a [NetworkResult].
 * Maps HTTP errors (via [okhttp3.Response]) and generic exceptions.
 *
 * Usage:
 *   val result = safeApiCall { apiService.sendMessage(request) }
 */
suspend fun <T> safeApiCall(block: suspend () -> T): NetworkResult<T> =
    try {
        NetworkResult.Success(block())
    } catch (e: retrofit2.HttpException) {
        val code    = e.code()
        val message = when (code) {
            401  -> "Invalid API key. Check your key in Settings."
            403  -> "Access denied. Verify your API key permissions."
            429  -> "Rate limit exceeded. Please wait and try again."
            500, 529 -> "Claude API is temporarily unavailable. Try again shortly."
            else -> "API error $code: ${e.message()}"
        }
        NetworkResult.Error(code = code, message = message, cause = e)
    } catch (e: java.net.UnknownHostException) {
        NetworkResult.Error(message = "No internet connection.", cause = e)
    } catch (e: java.net.SocketTimeoutException) {
        NetworkResult.Error(message = "Connection timed out. Check your network.", cause = e)
    } catch (e: java.io.IOException) {
        NetworkResult.Error(message = "Network error: ${e.message ?: "Unknown IO error"}", cause = e)
    } catch (e: kotlinx.serialization.SerializationException) {
        NetworkResult.Error(message = "Failed to parse server response.", cause = e)
    } catch (e: Exception) {
        NetworkResult.Error(message = "Unexpected error: ${e.message ?: "Unknown"}", cause = e)
    }
