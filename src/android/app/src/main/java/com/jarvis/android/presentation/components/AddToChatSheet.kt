package com.jarvis.android.presentation.components

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

/**
 * Claude-style "Add to chat" bottom sheet, triggered from the `+` button in
 * [JarvisInputBar]. Top row is three big attach tiles (Camera / Photos /
 * Files); below that are capability toggles the app actually has (Tools,
 * Web search, Root).
 *
 * @param tools        Current state of the "tools enabled" capability.
 * @param webSearch    Current state of the web-search capability.
 * @param rootMode     Current state of root mode.
 * @param onToolsChange / onWebSearchChange / onRootChange — toggle callbacks.
 * @param onPickPhoto  Called with a content:// URI when the user picks from
 *                     the gallery. Passes null if the picker was cancelled.
 * @param onCapture    Called with a content:// URI after a camera capture.
 * @param onPickFile   Called with a content:// URI for a generic file.
 * @param onDismiss    Hide the sheet.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AddToChatSheet(
    tools:           Boolean,
    webSearch:       Boolean,
    rootMode:        Boolean,
    onToolsChange:       (Boolean) -> Unit,
    onWebSearchChange:   (Boolean) -> Unit,
    onRootChange:        (Boolean) -> Unit,
    onPickPhoto:     (android.net.Uri?) -> Unit,
    onCapture:       (android.net.Uri?) -> Unit,
    onPickFile:      (android.net.Uri?) -> Unit,
    onDismiss:       () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope      = rememberCoroutineScope()

    val ctx = androidx.compose.ui.platform.LocalContext.current

    val photoPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        onPickPhoto(uri)
        scope.launch { sheetState.hide(); onDismiss() }
    }

    // Camera — TakePicturePreview gets a Bitmap back. Persist to cache, emit
    // a file:// Uri so the caller can base64-encode or pass along.
    val cameraLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.TakePicturePreview(),
    ) { bmp ->
        val uri = bmp?.let {
            val file = java.io.File(ctx.cacheDir, "capture-${System.currentTimeMillis()}.jpg")
            file.outputStream().use { out ->
                it.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, out)
            }
            android.net.Uri.fromFile(file)
        }
        onCapture(uri)
        scope.launch { sheetState.hide(); onDismiss() }
    }

    // CAMERA permission must be granted at runtime on Android 6+. If the
    // user hasn't granted it, launching the camera Intent raises a
    // SecurityException and crashes the app. Route through a permission
    // launcher, then fire the camera on grant.
    val cameraPermLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) cameraLauncher.launch(null)
        else {
            android.widget.Toast.makeText(ctx,
                "Camera permission denied",
                android.widget.Toast.LENGTH_SHORT).show()
            scope.launch { sheetState.hide(); onDismiss() }
        }
    }

    val filePicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent(),
    ) { uri ->
        onPickFile(uri)
        scope.launch { sheetState.hide(); onDismiss() }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState       = sheetState,
        containerColor   = Color(0xFF1A1A1A),
        dragHandle       = null,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 16.dp, vertical = 8.dp),
        ) {
            // ── Header row: close ✕  + title ──────────────────────────────
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onDismiss) {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = "Close",
                        tint = Color(0xFFECECEC),
                    )
                }
                Spacer(Modifier.weight(1f))
                Text(
                    "Add to chat",
                    style = MaterialTheme.typography.titleMedium.copy(
                        fontWeight = FontWeight.SemiBold,
                    ),
                    color = Color(0xFFECECEC),
                )
                Spacer(Modifier.weight(1f))
                // Empty slot on the right to balance the × on the left.
                Spacer(Modifier.size(48.dp))
            }

            Spacer(Modifier.height(12.dp))

            // ── Three big tiles: Camera / Photos / Files ──────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                AttachTile(
                    label   = "Camera",
                    icon    = Icons.Default.PhotoCamera,
                    onClick = {
                        val granted = ctx.checkSelfPermission(
                            android.Manifest.permission.CAMERA,
                        ) == android.content.pm.PackageManager.PERMISSION_GRANTED
                        if (granted) cameraLauncher.launch(null)
                        else cameraPermLauncher.launch(android.Manifest.permission.CAMERA)
                    },
                    modifier = Modifier.weight(1f),
                )
                AttachTile(
                    label   = "Photos",
                    icon    = Icons.Default.PhotoLibrary,
                    onClick = {
                        photoPicker.launch(
                            androidx.activity.result.PickVisualMediaRequest(
                                ActivityResultContracts.PickVisualMedia.ImageOnly,
                            ),
                        )
                    },
                    modifier = Modifier.weight(1f),
                )
                AttachTile(
                    label   = "Files",
                    icon    = Icons.Default.AttachFile,
                    onClick = { filePicker.launch("*/*") },
                    modifier = Modifier.weight(1f),
                )
            }

            Spacer(Modifier.height(18.dp))
            HorizontalDivider(color = Color(0xFF2A2A2A))
            Spacer(Modifier.height(6.dp))

            // ── Capability toggles ────────────────────────────────────────
            ToggleRow(
                icon    = Icons.Default.Search,
                label   = "Research",
                value   = tools,
                onChange = onToolsChange,
            )
            ToggleRow(
                icon    = Icons.Default.Public,
                label   = "Web search",
                value   = webSearch,
                onChange = onWebSearchChange,
            )
            ToggleRow(
                icon    = Icons.Default.Shield,
                label   = "Root mode",
                value   = rootMode,
                onChange = onRootChange,
                badge   = "DEVICE",
            )

            Spacer(Modifier.height(10.dp))
            HorizontalDivider(color = Color(0xFF2A2A2A))
            Spacer(Modifier.height(6.dp))

            Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    Icons.Default.Build,
                    contentDescription = null,
                    tint = Color(0xFF8A8A8A),
                    modifier = Modifier.size(20.dp),
                )
                Spacer(Modifier.width(14.dp))
                Column {
                    Text("Add to project", color = Color(0xFFECECEC))
                    Text("None", style = MaterialTheme.typography.bodySmall,
                         color = Color(0xFF8A8A8A))
                }
            }
        }
    }
}

@Composable
private fun AttachTile(
    label:    String,
    icon:     ImageVector,
    onClick:  () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .height(90.dp)
            .background(Color(0xFF222222), RoundedCornerShape(14.dp))
            .border(1.dp, Color(0xFF2E2E2E), RoundedCornerShape(14.dp))
            .clickable(onClick = onClick)
            .padding(vertical = 14.dp, horizontal = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            icon,
            contentDescription = null,
            tint = Color(0xFFECECEC),
            modifier = Modifier.size(24.dp),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = Color(0xFFECECEC),
        )
    }
}

@Composable
private fun ToggleRow(
    icon:     ImageVector,
    label:    String,
    value:    Boolean,
    onChange: (Boolean) -> Unit,
    badge:    String? = null,
) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            icon,
            contentDescription = null,
            tint = Color(0xFFECECEC),
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(14.dp))
        Text(label, color = Color(0xFFECECEC), fontSize = 15.sp)
        if (badge != null) {
            Spacer(Modifier.width(8.dp))
            Box(
                modifier = Modifier
                    .background(Color(0xFF2A2A2A), RoundedCornerShape(4.dp))
                    .padding(horizontal = 6.dp, vertical = 2.dp),
            ) {
                Text(
                    badge,
                    fontSize = 10.sp,
                    color = Color(0xFF8A8A8A),
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
        Spacer(Modifier.weight(1f))
        Switch(
            checked = value,
            onCheckedChange = onChange,
            colors = SwitchDefaults.colors(
                checkedTrackColor   = Color(0xFF1E7FFF),
                uncheckedTrackColor = Color(0xFF2A2A2A),
            ),
        )
    }
}
