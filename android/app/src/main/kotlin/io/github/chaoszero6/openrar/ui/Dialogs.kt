package io.github.chaoszero6.openrar.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import io.github.chaoszero6.openrar.Dialog
import io.github.chaoszero6.openrar.MainViewModel
import io.github.chaoszero6.openrar.TaskState
import io.github.chaoszero6.openrar.core.CreateOptions
import io.github.chaoszero6.openrar.core.Format
import io.github.chaoszero6.openrar.core.LEVEL_NAMES
import io.github.chaoszero6.openrar.core.OverwriteAnswer
import io.github.chaoszero6.openrar.core.OverwriteMode
import io.github.chaoszero6.openrar.core.Paths
import java.io.File

@Composable
fun DialogHost(vm: MainViewModel) {
    when (val d = vm.dialog) {
        null -> Unit
        is Dialog.Password -> PasswordDialog(d, vm)
        is Dialog.Overwrite -> OverwriteDialog(d, vm)
        is Dialog.Extract -> ExtractDialog(d, vm)
        is Dialog.Create -> CreateDialog(d, vm)
        is Dialog.Info -> InfoDialog(d, vm)
        is Dialog.Message -> AlertDialog(
            onDismissRequest = { vm.dismissDialog() }, title = { Text(d.title) },
            text = { Text(d.text, Modifier.verticalScroll(rememberScrollState())) },
            confirmButton = { TextButton(onClick = { vm.dismissDialog() }) { Text("OK") } },
        )
        is Dialog.Confirm -> AlertDialog(
            onDismissRequest = { vm.dismissDialog() }, title = { Text(d.title) },
            text = { Text(d.text, Modifier.verticalScroll(rememberScrollState())) },
            confirmButton = { TextButton(onClick = { vm.dialog = null; d.onConfirm() }) { Text(d.confirm) } },
            dismissButton = { TextButton(onClick = { vm.dismissDialog() }) { Text("Cancel") } },
        )
        is Dialog.Rename -> {
            var name by remember(d) { mutableStateOf(d.current) }
            AlertDialog(
                onDismissRequest = { vm.dismissDialog() }, title = { Text(d.title) },
                text = { OutlinedTextField(name, { name = it }, singleLine = true, label = { Text("Name") }) },
                confirmButton = {
                    TextButton(enabled = name.isNotBlank() && name != d.current, onClick = { vm.dialog = null; d.onRename(name) }) { Text("OK") }
                },
                dismissButton = { TextButton(onClick = { vm.dismissDialog() }) { Text("Cancel") } },
            )
        }
    }
}

@Composable
private fun PasswordDialog(d: Dialog.Password, vm: MainViewModel) {
    var pw by remember(d) { mutableStateOf("") }
    var show by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = { vm.dismissDialog() },
        title = { Text(if (d.wrong) "Wrong password" else "Enter password") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (d.wrong) "The password for ${d.name} is not correct. Try again." else "${d.name} is encrypted.")
                PasswordField(pw, { pw = it }, "Password", show) { show = !show }
            }
        },
        confirmButton = {
            TextButton(enabled = pw.isNotEmpty(), onClick = {
                val r = pw.toCharArray()
                vm.dialog = null
                d.onResult(r)
            }) { Text("OK") }
        },
        dismissButton = { TextButton(onClick = { vm.dismissDialog() }) { Text("Cancel") } },
    )
}

@Composable
private fun PasswordField(value: String, onChange: (String) -> Unit, label: String, show: Boolean, toggle: () -> Unit) {
    OutlinedTextField(
        value, onChange, singleLine = true, label = { Text(label) }, modifier = Modifier.fillMaxWidth(),
        visualTransformation = if (show) VisualTransformation.None else PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        trailingIcon = {
            IconButton(onClick = toggle) {
                Icon(if (show) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility, if (show) "Hide" else "Show")
            }
        },
    )
}

@Composable
private fun OverwriteDialog(d: Dialog.Overwrite, vm: MainViewModel) {
    fun answer(a: OverwriteAnswer) {
        vm.dialog = null
        d.answer.complete(a)
    }
    AlertDialog(
        onDismissRequest = { answer(OverwriteAnswer.CANCEL) },
        properties = DialogProperties(dismissOnClickOutside = false),
        title = { Text("Replace file?") },
        text = {
            Text("${d.target.name} already exists in ${d.target.parentFile?.name}.\n\n" +
                "Existing: ${Paths.humanSize(d.target.length())}\nIn the archive: ${Paths.humanSize(d.entry.size)}")
        },
        confirmButton = {
            Column(horizontalAlignment = Alignment.End) {
                TextButton(onClick = { answer(OverwriteAnswer.YES) }) { Text("Replace") }
                TextButton(onClick = { answer(OverwriteAnswer.YES_ALL) }) { Text("Replace all") }
                TextButton(onClick = { answer(OverwriteAnswer.RENAME) }) { Text("Keep both") }
                TextButton(onClick = { answer(OverwriteAnswer.NO) }) { Text("Skip") }
                TextButton(onClick = { answer(OverwriteAnswer.NO_ALL) }) { Text("Skip all") }
                TextButton(onClick = { answer(OverwriteAnswer.CANCEL) }) { Text("Cancel") }
            }
        },
    )
}

@Composable
private fun Choice(selected: Boolean, label: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().selectable(selected, onClick = onClick).padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected, onClick)
        Text(label)
    }
}

@Composable
private fun Check(checked: Boolean, label: String, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(enabled) { onChange(!checked) },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked, onChange, enabled = enabled)
        Text(label, color = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun ExtractDialog(d: Dialog.Extract, vm: MainViewModel) {
    var where by remember(d) { mutableStateOf(if (d.useSubfolder) 1 else 0) }
    var custom by remember(d) { mutableStateOf(d.subfolder.absolutePath) }
    var mode by remember(d) { mutableStateOf(OverwriteMode.ASK) }
    var keepPaths by remember(d) { mutableStateOf(true) }
    AlertDialog(
        onDismissRequest = { vm.dismissDialog() },
        title = { Text(if (d.selection == null) "Extract all" else "Extract ${d.selection.size} item(s)") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text("Destination", fontWeight = FontWeight.SemiBold)
                Choice(where == 0, "Here: ${d.here.name}/") { where = 0 }
                Choice(where == 1, "To folder: ${d.subfolder.name}/") { where = 1 }
                Choice(where == 2, "Other folder") { where = 2 }
                if (where == 2) OutlinedTextField(custom, { custom = it }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("When a file already exists", fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp))
                Choice(mode == OverwriteMode.ASK, "Ask") { mode = OverwriteMode.ASK }
                Choice(mode == OverwriteMode.OVERWRITE, "Replace") { mode = OverwriteMode.OVERWRITE }
                Choice(mode == OverwriteMode.SKIP, "Skip") { mode = OverwriteMode.SKIP }
                Choice(mode == OverwriteMode.RENAME, "Keep both (rename)") { mode = OverwriteMode.RENAME }
                Check(keepPaths, "Keep folder structure") { keepPaths = it }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val dest = when (where) {
                    0 -> d.here
                    1 -> d.subfolder
                    else -> File(custom.trim())
                }
                vm.dialog = null
                vm.extract(d.selection, dest, mode, keepPaths, d.stripPrefix)
            }) { Text("Extract") }
        },
        dismissButton = { TextButton(onClick = { vm.dismissDialog() }) { Text("Cancel") } },
    )
}

private val VOLUME_PRESETS = listOf(
    "No splitting" to 0L, "10 MB" to (10L shl 20), "25 MB (e-mail)" to (25L shl 20), "100 MB" to (100L shl 20),
    "700 MB (CD)" to (700L shl 20), "2 GB" to (2L shl 30), "4 GB (FAT32)" to (4L * 1024 * 1024 * 1024 - 1))

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun <T> Dropdown(label: String, options: List<Pair<String, T>>, value: T, enabled: Boolean = true, onChange: (T) -> Unit) {
    var open by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(open, { if (enabled) open = it }) {
        OutlinedTextField(
            options.first { it.second == value }.first, {}, readOnly = true, enabled = enabled, label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(open) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        ExposedDropdownMenu(open, { open = false }) {
            for ((text, v) in options) DropdownMenuItem(text = { Text(text) }, onClick = { onChange(v); open = false })
        }
    }
}

@Composable
private fun CreateDialog(d: Dialog.Create, vm: MainViewModel) {
    var name by remember(d) { mutableStateOf(d.defaultName) }
    var format by remember(d) { mutableStateOf(Format.ZIP) }
    var level by remember(d) { mutableStateOf(3) }
    var password by remember(d) { mutableStateOf("") }
    var password2 by remember(d) { mutableStateOf("") }
    var showPw by remember { mutableStateOf(false) }
    var volume by remember(d) { mutableStateOf(0L) }
    var deleteAfter by remember(d) { mutableStateOf(false) }
    var testAfter by remember(d) { mutableStateOf(false) }
    var addToExisting by remember(d) { mutableStateOf(true) }
    val fileName = "${name.trim()}.${format.extension}"
    val dest = File(d.destDir, fileName)
    val exists = dest.exists()
    val canAdd = exists && format == Format.ZIP && volume == 0L
    val pwOk = password == password2 || showPw
    AlertDialog(
        onDismissRequest = { vm.dismissDialog() },
        title = { Text("Create archive") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()).heightIn(max = 520.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("${d.sources.size} item(s) → ${d.destDir.name}/", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(name, { name = it }, singleLine = true, label = { Text("Archive name") },
                    suffix = { Text(".${format.extension}") }, modifier = Modifier.fillMaxWidth())
                Dropdown("Format", Format.entries.map { it.label to it }, format) { format = it }
                Dropdown("Compression", LEVEL_NAMES.mapIndexed { i, n -> n to i }, level) { level = it }
                Dropdown("Split into volumes", VOLUME_PRESETS, volume) { volume = it }
                if (format.canEncrypt) {
                    PasswordField(password, { password = it }, "Password (optional, AES-256)", showPw) { showPw = !showPw }
                    if (!showPw && password.isNotEmpty()) {
                        OutlinedTextField(password2, { password2 = it }, singleLine = true, label = { Text("Repeat password") },
                            visualTransformation = PasswordVisualTransformation(), isError = !pwOk, modifier = Modifier.fillMaxWidth())
                    }
                } else {
                    Text("Passwords are available for ZIP archives on Android.", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Check(testAfter, "Test the archive afterwards") { testAfter = it }
                Check(deleteAfter, "Delete the files after archiving") { deleteAfter = it }
                if (exists) {
                    if (canAdd) Check(addToExisting, "Add to the existing $fileName") { addToExisting = it }
                    if (!canAdd || !addToExisting) Text("$fileName already exists and will be replaced.",
                        color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank() && !name.contains('/') && pwOk, onClick = {
                val opts = CreateOptions(format, level, password.takeIf { it.isNotEmpty() && format.canEncrypt }?.toCharArray(),
                    volume, deleteAfter, testAfter)
                vm.dialog = null
                vm.create(d.sources, dest, opts, canAdd && addToExisting)
            }) { Text("Create") }
        },
        dismissButton = { TextButton(onClick = { vm.dismissDialog() }) { Text("Cancel") } },
    )
}

@Composable
private fun InfoDialog(d: Dialog.Info, vm: MainViewModel) {
    val i = d.info
    val ratio = if (i.unpackedSize > 0) "${(i.packedSize * 100 / i.unpackedSize)}%" else "-"
    val rows = listOf(
        "Format" to i.format, "Files" to "${i.files}", "Folders" to "${i.folders}",
        "Size" to Paths.humanSize(i.unpackedSize), "Packed" to Paths.humanSize(i.packedSize), "Ratio" to ratio,
        "Encrypted" to if (i.encrypted) "yes" + (if (i.namesEncrypted) " (file names too)" else "") else "no",
        "Volumes" to "${i.volumes}",
        "Self-extracting" to if (i.selfExtracting) "yes" else "no", "Locked" to if (i.locked) "yes" else "no",
    ).let { r -> if (i.solid) r + ("Solid" to "yes") else r }     // 7Z readers can't always tell, so only "yes" is shown
    AlertDialog(
        onDismissRequest = { vm.dismissDialog() }, title = { Text(d.name) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                for ((k, v) in rows) Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                    Text(k, Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(v, Modifier.weight(1.4f))
                }
                if (i.comment.isNotEmpty()) {
                    Text("Comment", fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp))
                    Text(i.comment, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = { TextButton(onClick = { vm.dismissDialog() }) { Text("Close") } },
    )
}

@Composable
fun ProgressDialog(t: TaskState, onCancel: () -> Unit) {
    AlertDialog(
        onDismissRequest = {},
        properties = DialogProperties(dismissOnBackPress = false, dismissOnClickOutside = false),
        title = { Text(t.title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(t.file.substringAfterLast('/').ifEmpty { "Please wait…" }, maxLines = 1, style = MaterialTheme.typography.bodySmall)
                if (t.determinate && t.total > 0) {
                    LinearProgressIndicator(progress = { (t.done.toFloat() / t.total).coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
                    Text("${Paths.humanSize(t.done)} of ${Paths.humanSize(t.total)}", style = MaterialTheme.typography.bodySmall)
                } else {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
            }
        },
        confirmButton = { TextButton(onClick = onCancel, enabled = !t.cancelled) { Text("Cancel") } },
    )
}
