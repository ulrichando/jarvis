package com.jarvis.android.presentation.settings

/**
 * Catalog of TTS voices available via Groq's `/openai/v1/audio/speech`
 * endpoint.
 *
 * ## Model lineage
 *
 * Groq originally hosted PlayAI's `playai-tts` model with voices whose IDs
 * ended in `-PlayAI` (Fritz-PlayAI, Atlas-PlayAI, etc). That model was
 * **decommissioned in April 2026** — requests to it return HTTP 400 with
 * `code: model_decommissioned`. Groq migrated TTS to **Canopy Labs'
 * Orpheus v1** (`canopylabs/orpheus-v1-english`), which ships six
 * professionally-trained voice personas:
 *   - Male:   austin, daniel, troy
 *   - Female: autumn, diana, hannah
 *
 * The voice IDs below are the exact strings Orpheus accepts. See
 * [GroqTtsClient] for the model name + request shape, and
 * [migrateLegacyVoiceId] for how pref values saved under the old PlayAI
 * scheme are upgraded silently on first launch.
 */
data class GroqTtsVoice(
    val id:          String,
    val label:       String,
    val gender:      Gender,
    val description: String,
) {
    enum class Gender { MALE, FEMALE }

    companion object {
        val CATALOG: List<GroqTtsVoice> = listOf(
            GroqTtsVoice(
                id          = "troy",
                label       = "Troy",
                gender      = Gender.MALE,
                description = "Warm, articulate male — JARVIS default",
            ),
            GroqTtsVoice(
                id          = "austin",
                label       = "Austin",
                gender      = Gender.MALE,
                description = "Smooth, confident male",
            ),
            GroqTtsVoice(
                id          = "daniel",
                label       = "Daniel",
                gender      = Gender.MALE,
                description = "Measured, clear male",
            ),
            GroqTtsVoice(
                id          = "hannah",
                label       = "Hannah",
                gender      = Gender.FEMALE,
                description = "Expressive female",
            ),
            GroqTtsVoice(
                id          = "diana",
                label       = "Diana",
                gender      = Gender.FEMALE,
                description = "Clear, professional female",
            ),
            GroqTtsVoice(
                id          = "autumn",
                label       = "Autumn",
                gender      = Gender.FEMALE,
                description = "Warm, natural female",
            ),
        )

        const val DEFAULT_VOICE = "troy"

        fun labelFor(id: String): String =
            CATALOG.firstOrNull { it.id == id }?.label ?: id

        /**
         * Map a legacy PlayAI voice ID (e.g. `Fritz-PlayAI`) to the closest
         * Orpheus equivalent. Users who upgraded from the pre-decommission
         * build have a PlayAI ID saved in prefs; calling Orpheus with that
         * string produces a 400. This table gets them back to working audio
         * without forcing a manual re-pick.
         */
        fun migrateLegacyVoiceId(old: String): String = when (old) {
            "Fritz-PlayAI"   -> "troy"     // warm male → warm male
            "Atlas-PlayAI"   -> "daniel"   // deep male → measured male
            "Basil-PlayAI"   -> "troy"     // British-adj male → warm male
            "Mason-PlayAI"   -> "austin"   // smooth male
            "Thunder-PlayAI" -> "daniel"   // dramatic male
            "Chip-PlayAI"    -> "austin"   // friendly male
            "Briggs-PlayAI"  -> "daniel"   // calm male
            "Mikail-PlayAI"  -> "austin"   // soft male
            "Celeste-PlayAI" -> "autumn"   // warm female
            "Quinn-PlayAI"   -> "diana"    // clear female
            else             -> if (CATALOG.any { it.id == old }) old else DEFAULT_VOICE
        }
    }
}
