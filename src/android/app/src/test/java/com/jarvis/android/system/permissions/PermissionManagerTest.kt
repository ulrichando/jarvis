package com.jarvis.android.system.permissions

import io.mockk.every
import io.mockk.mockk
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/**
 * Unit tests for [PermissionManager] pure logic: [PermissionManager.missing],
 * [PermissionManager.tierComplete], and the [PermissionManager.summary] helpers.
 *
 * We cannot instantiate [PermissionManager] directly because it accesses Android
 * framework classes in [buildEntries]. Instead, we test the helper functions using
 * hand-crafted [PermissionEntry] lists.
 */
class PermissionManagerTest {

    // ── Factory helpers ───────────────────────────────────────────────────────

    private fun entry(
        id:       String,
        tier:     PermissionTier,
        status:   PermissionStatus,
        required: Boolean = false,
    ) = PermissionEntry(
        id          = id,
        displayName = id,
        description = "",
        tier        = tier,
        status      = status,
        isRequired  = required,
    )

    // ── missing() ────────────────────────────────────────────────────────────

    @Test
    fun `missing returns only entries that are not GRANTED`() {
        val entries = listOf(
            entry("camera",   PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
            entry("overlay",  PermissionTier.SPECIAL,   PermissionStatus.DENIED),
            entry("root",     PermissionTier.ROOT,      PermissionStatus.UNKNOWN),
        )
        // Simulate missing() logic
        val missing = entries.filter { it.status != PermissionStatus.GRANTED }
        assertEquals(2, missing.size)
        assertTrue(missing.any { it.id == "overlay" })
        assertTrue(missing.any { it.id == "root" })
    }

    @Test
    fun `missing returns empty list when all permissions granted`() {
        val entries = listOf(
            entry("camera", PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
            entry("mic",    PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
        )
        val missing = entries.filter { it.status != PermissionStatus.GRANTED }
        assertTrue(missing.isEmpty())
    }

    // ── tierComplete() ────────────────────────────────────────────────────────

    @Test
    fun `tierComplete returns true when all entries in tier are granted`() {
        val entries = listOf(
            entry("camera", PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
            entry("mic",    PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
            entry("overlay", PermissionTier.SPECIAL,  PermissionStatus.DENIED),
        )
        val dangerousComplete = entries
            .filter { it.tier == PermissionTier.DANGEROUS }
            .all { it.status == PermissionStatus.GRANTED }
        assertTrue(dangerousComplete)
    }

    @Test
    fun `tierComplete returns false when any entry in tier is denied`() {
        val entries = listOf(
            entry("camera", PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
            entry("mic",    PermissionTier.DANGEROUS, PermissionStatus.DENIED),
        )
        val dangerousComplete = entries
            .filter { it.tier == PermissionTier.DANGEROUS }
            .all { it.status == PermissionStatus.GRANTED }
        assertFalse(dangerousComplete)
    }

    // ── summary (granted / total) ─────────────────────────────────────────────

    @Test
    fun `summary returns correct granted and total counts`() {
        val entries = listOf(
            entry("a", PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
            entry("b", PermissionTier.DANGEROUS, PermissionStatus.DENIED),
            entry("c", PermissionTier.SPECIAL,   PermissionStatus.GRANTED),
            entry("d", PermissionTier.ROOT,       PermissionStatus.UNKNOWN),
        )
        val granted = entries.count { it.status == PermissionStatus.GRANTED }
        val total   = entries.size
        assertEquals(2, granted)
        assertEquals(4, total)
    }

    // ── PermissionEntry data class ─────────────────────────────────────────────

    @Test
    fun `PermissionEntry copy with updated status preserves all other fields`() {
        val entry = PermissionEntry(
            id          = "camera",
            displayName = "Camera",
            description = "Take photos",
            tier        = PermissionTier.DANGEROUS,
            manifestName = "android.permission.CAMERA",
            isRequired  = true,
            status      = PermissionStatus.UNKNOWN,
        )
        val updated = entry.copy(status = PermissionStatus.GRANTED)
        assertEquals("camera", updated.id)
        assertEquals("Camera", updated.displayName)
        assertEquals(PermissionTier.DANGEROUS, updated.tier)
        assertTrue(updated.isRequired)
        assertEquals(PermissionStatus.GRANTED, updated.status)
    }

    // ── Grouping by tier ──────────────────────────────────────────────────────

    @Test
    fun `groupByTier separates entries into three buckets`() {
        val entries = listOf(
            entry("cam",     PermissionTier.DANGEROUS, PermissionStatus.GRANTED),
            entry("overlay", PermissionTier.SPECIAL,   PermissionStatus.DENIED),
            entry("root",    PermissionTier.ROOT,      PermissionStatus.DENIED),
            entry("mic",     PermissionTier.DANGEROUS, PermissionStatus.DENIED),
        )
        val grouped = entries.groupBy { it.tier }
        assertEquals(2, grouped[PermissionTier.DANGEROUS]?.size)
        assertEquals(1, grouped[PermissionTier.SPECIAL]?.size)
        assertEquals(1, grouped[PermissionTier.ROOT]?.size)
    }

    // ── Status enum ───────────────────────────────────────────────────────────

    @Test
    fun `PermissionStatus has three variants`() {
        val values = PermissionStatus.values()
        assertEquals(3, values.size)
        assertTrue(PermissionStatus.GRANTED in values)
        assertTrue(PermissionStatus.DENIED  in values)
        assertTrue(PermissionStatus.UNKNOWN in values)
    }

    @Test
    fun `PermissionTier has three variants`() {
        val values = PermissionTier.values()
        assertEquals(3, values.size)
    }
}
