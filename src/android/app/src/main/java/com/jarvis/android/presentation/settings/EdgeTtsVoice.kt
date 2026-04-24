package com.jarvis.android.presentation.settings

/**
 * Catalog of Edge TTS voices exposed in Settings.
 *
 * These are Microsoft's free Edge Read-Aloud neural voices. Every id here
 * works with [com.jarvis.android.presentation.chat.EdgeTtsClient] — no
 * server setup, no API key. Ordering puts the JARVIS-tone defaults first.
 */
data class EdgeTtsVoice(
    val id:          String,
    val label:       String,
    val gender:      Gender,
    val region:      String,
    val description: String,
) {
    enum class Gender { MALE, FEMALE }

    companion object {
        val CATALOG: List<EdgeTtsVoice> = listOf(
            EdgeTtsVoice(
                id = "en-GB-RyanNeural",
                label = "Ryan (British)",
                gender = Gender.MALE,
                region = "UK",
                description = "Warm British male — JARVIS default",
            ),
            EdgeTtsVoice(
                id = "en-GB-ThomasNeural",
                label = "Thomas (British)",
                gender = Gender.MALE,
                region = "UK",
                description = "Deep British male",
            ),
            EdgeTtsVoice(
                id = "en-US-GuyNeural",
                label = "Guy (American)",
                gender = Gender.MALE,
                region = "US",
                description = "Friendly American male",
            ),
            EdgeTtsVoice(
                id = "en-US-ChristopherNeural",
                label = "Christopher (American)",
                gender = Gender.MALE,
                region = "US",
                description = "Deep, authoritative American male",
            ),
            EdgeTtsVoice(
                id = "en-US-EricNeural",
                label = "Eric (American)",
                gender = Gender.MALE,
                region = "US",
                description = "Neutral American male",
            ),
            EdgeTtsVoice(
                id = "en-US-DavisNeural",
                label = "Davis (American)",
                gender = Gender.MALE,
                region = "US",
                description = "Calm American male",
            ),
            EdgeTtsVoice(
                id = "en-US-BrianMultilingualNeural",
                label = "Brian (multilingual)",
                gender = Gender.MALE,
                region = "US",
                description = "American male — handles non-English cleanly",
            ),
            EdgeTtsVoice(
                id = "en-GB-SoniaNeural",
                label = "Sonia (British)",
                gender = Gender.FEMALE,
                region = "UK",
                description = "British female",
            ),
            EdgeTtsVoice(
                id = "en-US-JennyMultilingualNeural",
                label = "Jenny (multilingual)",
                gender = Gender.FEMALE,
                region = "US",
                description = "American female — multilingual",
            ),
            EdgeTtsVoice(
                id = "en-US-AvaMultilingualNeural",
                label = "Ava (multilingual)",
                gender = Gender.FEMALE,
                region = "US",
                description = "Warm American female — multilingual",
            ),
        )

        fun labelFor(id: String): String =
            CATALOG.firstOrNull { it.id == id }?.label ?: id
    }
}
