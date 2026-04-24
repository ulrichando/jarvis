package com.jarvis.android.presentation.components

import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisPalette

/**
 * Termux-style extra-keys bar pinned above the soft keyboard. Sends escape
 * sequences and control characters into the PTY via [onInput]. Ctrl is a
 * visual latch; the three Ctrl-letter shortcuts (C, D, Z) are exposed as
 * dedicated keys because Android IMEs do not deliver raw key events to
 * hidden input fields reliably. Paste pulls from the system clipboard.
 */
@Composable
fun TerminalExtraKeys(
    onInput:  (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var ctrlLatched by remember { mutableStateOf(false) }
    val ctx = LocalContext.current

    // Build raw-byte strings so Kotlin escape parsing cannot mangle them.
    val esc = byteArrayOf(0x1B).toString(Charsets.ISO_8859_1)
    val csi = byteArrayOf(0x1B, 0x5B).toString(Charsets.ISO_8859_1)

    fun send(s: String) {
        onInput(s)
        if (ctrlLatched) ctrlLatched = false
    }

    fun sendCtrlLetter(letterUpper: Char) {
        val code = (letterUpper.code - 'A'.code + 1).toByte()
        onInput(byteArrayOf(code).toString(Charsets.ISO_8859_1))
        ctrlLatched = false
    }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(44.dp)
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 4.dp, vertical = 4.dp),
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Key("ESC")   { send(esc) }
        Key("TAB")   { send("\t") }
        Key("CTRL", highlighted = ctrlLatched) { ctrlLatched = !ctrlLatched }
        Key("-")     { send("-") }
        Key("/")     { send("/") }
        Key("|")     { send("|") }
        Key("←")     { send(csi + "D") }
        Key("↑")     { send(csi + "A") }
        Key("↓")     { send(csi + "B") }
        Key("→")     { send(csi + "C") }
        Key("HOME")  { send(csi + "H") }
        Key("END")   { send(csi + "F") }
        Key("PGUP")  { send(csi + "5~") }
        Key("PGDN")  { send(csi + "6~") }
        Key("⎈C")    { sendCtrlLetter('C') }
        Key("⎈D")    { sendCtrlLetter('D') }
        Key("⎈Z")    { sendCtrlLetter('Z') }
        Key("PASTE") {
            val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
            val text = cm?.primaryClip
                ?.takeIf { it.itemCount > 0 }
                ?.getItemAt(0)
                ?.coerceToText(ctx)
                ?.toString()
            if (!text.isNullOrEmpty()) send(text)
        }
    }
}

@Composable
private fun Key(
    label: String,
    highlighted: Boolean = false,
    onTap: () -> Unit,
) {
    Box(
        modifier = Modifier
            .sizeIn(minWidth = 38.dp, minHeight = 36.dp)
            .background(
                color = if (highlighted) JarvisPalette.GoldPrimary.copy(alpha = 0.28f)
                        else MaterialTheme.colorScheme.surfaceContainerHigh,
                shape = RoundedCornerShape(6.dp),
            )
            .clickable(onClick = onTap)
            .padding(horizontal = 10.dp, vertical = 4.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text       = label,
            style      = MaterialTheme.typography.labelMedium,
            fontFamily = FontFamily.Monospace,
            fontWeight = if (highlighted) FontWeight.Bold else FontWeight.Medium,
            color      = if (highlighted) JarvisPalette.GoldPrimary
                         else MaterialTheme.colorScheme.onSurface,
        )
    }
}
