package com.jarvis.android.presentation.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.android.R
import java.util.Calendar

/**
 * Retained for Home body callers, but the new minimalist design has no
 * tappable prompt tiles — this model exists purely so the ChatScreen's
 * auto-send-on-prompt-tap callback signature stays stable if we re-enable
 * them later.
 */
data class SuggestedPrompt(
    val label:  String,
    val prompt: String,
)

private val Accent       = Color(0xFF1E7FFF)
private val TextHero     = Color(0xFFECECEC)
private val TextSecondary = Color(0xFF8A8A8A)

/**
 * Empty-state hero.
 *
 * Heavily inspired by the Claude mobile app: a small brand mark floating above
 * a large serif greeting, nothing else. No suggested prompts, no dense
 * affordances — the composition is the message, and the input bar below does
 * the rest.
 *
 * ```
 *                          ·●·
 *
 *              How may I help you this
 *                     evening?
 * ```
 *
 * [onPromptClick] is kept in the signature but is intentionally unused here
 * so the Home composition can wire in suggested prompts later without a
 * second refactor.
 */
@Composable
fun HomeHero(
    userName:      String? = null,
    onPromptClick: (SuggestedPrompt) -> Unit,
    modifier:      Modifier = Modifier,
) {
    // Keep the parameter referenced so Kotlin doesn't flag it as unused.
    @Suppress("UNUSED_EXPRESSION") onPromptClick

    val greeting = remember(userName) { buildGreeting(userName) }

    Column(
        modifier            = modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        // ── Brand mark — small 2D JARVIS silhouette tinted in accent ─────
        Box(
            modifier = Modifier
                .size(48.dp)
                .background(Accent.copy(alpha = 0.10f), CircleShape)
                .border(1.dp, Accent.copy(alpha = 0.28f), CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                painter            = painterResource(R.drawable.ic_jarvis_notification),
                contentDescription = null,
                tint               = Accent,
                modifier           = Modifier.size(22.dp),
            )
        }

        Spacer(Modifier.height(28.dp))

        // ── Greeting — serif, large, two lines centered ──────────────────
        Text(
            text  = greeting,
            style = MaterialTheme.typography.headlineMedium.copy(
                fontFamily = FontFamily.Serif,
                fontWeight = FontWeight.Normal,
                fontSize   = 30.sp,
                lineHeight = 38.sp,
            ),
            color     = TextHero,
            textAlign = TextAlign.Center,
            modifier  = Modifier
                .fillMaxWidth()
                .padding(horizontal = 32.dp),
        )
    }
}

/**
 * Builds a time-aware greeting that matches the serif-heavy feel of the
 * reference design ("How can I help you this evening?").
 *
 * The phrasing deliberately avoids the user's name in the main greeting so it
 * renders cleanly on two lines in portrait. If [userName] is supplied it's
 * appended as an optional short suffix — callers can pass null to skip it.
 */
private fun buildGreeting(userName: String?): String {
    val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
    // "tonight" reads more naturally than "this night" for the late hours.
    val suffix = when {
        hour in 5..11  -> "this morning"
        hour in 12..16 -> "this afternoon"
        hour in 17..21 -> "this evening"
        else           -> "tonight"
    }
    val base = "How can I help you $suffix?"
    return if (userName.isNullOrBlank()) base else "How can I help you $suffix, $userName?"
}
