package io.github.chaoszero6.openrar.ui

import android.content.Context
import android.text.format.DateFormat
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.InsertDriveFile
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CreateNewFolder
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.DriveFileRenameOutline
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.FolderZip
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.material.icons.outlined.SdStorage
import androidx.compose.material.icons.outlined.SelectAll
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.Unarchive
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.chaoszero6.openrar.MainViewModel
import io.github.chaoszero6.openrar.Storage
import io.github.chaoszero6.openrar.core.ArchiveTree
import io.github.chaoszero6.openrar.core.Archives
import io.github.chaoszero6.openrar.core.Paths
import java.util.Date

fun iconFor(name: String, isDir: Boolean): ImageVector {
    if (isDir) return Icons.Outlined.Folder
    val ext = name.substringAfterLast('.', "").lowercase()
    return when {
        Archives.looksLikeArchive(name) -> Icons.Outlined.FolderZip
        ext in setOf("jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "svg") -> Icons.Outlined.Image
        ext in setOf("mp4", "mkv", "avi", "mov", "webm", "3gp") -> Icons.Outlined.Movie
        ext in setOf("mp3", "flac", "ogg", "m4a", "wav", "opus", "aac") -> Icons.Outlined.MusicNote
        ext in setOf("txt", "md", "pdf", "doc", "docx", "odt", "csv", "json", "xml", "html", "log") -> Icons.Outlined.Description
        else -> Icons.AutoMirrored.Outlined.InsertDriveFile
    }
}

fun formatDate(ctx: Context, millis: Long?): String {
    if (millis == null || millis <= 0) return ""
    val d = Date(millis)
    return DateFormat.getMediumDateFormat(ctx).format(d) + " " + DateFormat.getTimeFormat(ctx).format(d)
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun EntryRow(
    icon: ImageVector, title: String, subtitle: String, selected: Boolean, selecting: Boolean,
    onClick: () -> Unit, onLongClick: () -> Unit, trailing: (@Composable () -> Unit)? = null,
    accent: Boolean = false,
) {
    ListItem(
        modifier = Modifier
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .background(if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface),
        leadingContent = {
            Box(
                Modifier
                    .size(40.dp)
                    .background(
                        if (accent) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
                        RoundedCornerShape(10.dp),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, null, tint = if (accent) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        headlineContent = { Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        supportingContent = { if (subtitle.isNotEmpty()) Text(subtitle, maxLines = 1, style = MaterialTheme.typography.bodySmall) },
        trailingContent = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                trailing?.invoke()
                if (selecting) Checkbox(checked = selected, onCheckedChange = { onClick() })
            }
        },
        colors = ListItemDefaults.colors(containerColor = androidx.compose.ui.graphics.Color.Transparent),
    )
}

// ==================================================================== file browser
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FilesScreen(vm: MainViewModel, snackbar: SnackbarHostState, onRequestAccess: () -> Unit, onAbout: () -> Unit) {
    val ctx = LocalContext.current
    val selecting = vm.selectedFiles.isNotEmpty()
    var menu by remember { mutableStateOf(false) }
    var rootsMenu by remember { mutableStateOf(false) }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = if (selecting) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
                ),
                navigationIcon = {
                    when {
                        selecting -> IconButton(onClick = { vm.selectedFiles.clear() }) { Icon(Icons.Default.Close, "Clear selection") }
                        vm.canGoUp() -> IconButton(onClick = { vm.navigate(vm.dir.parentFile!!) }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Up") }
                    }
                },
                title = {
                    if (selecting) Text("${vm.selectedFiles.size} selected")
                    else Column {
                        Text(if (vm.canGoUp()) vm.dir.name else "OpenRAR", maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(vm.dir.absolutePath, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                },
                actions = {
                    if (selecting) {
                        IconButton(onClick = { vm.selectedFiles.clear(); vm.selectedFiles.addAll(vm.files) }) { Icon(Icons.Outlined.SelectAll, "Select all") }
                        if (vm.selectedFiles.size == 1) IconButton(onClick = { vm.renameFile(vm.selectedFiles[0]) }) { Icon(Icons.Outlined.DriveFileRenameOutline, "Rename") }
                        IconButton(onClick = { Storage.share(ctx, vm.selectedFiles.filter { it.isFile }) }) { Icon(Icons.Outlined.Share, "Share") }
                        IconButton(onClick = { vm.deleteSelectedFiles() }) { Icon(Icons.Outlined.Delete, "Delete") }
                    } else {
                        Box {
                            IconButton(onClick = { rootsMenu = true }) { Icon(Icons.Outlined.SdStorage, "Storage") }
                            DropdownMenu(rootsMenu, { rootsMenu = false }) {
                                for (r in Storage.roots(ctx)) DropdownMenuItem(text = { Text(r.label) }, onClick = { rootsMenu = false; vm.navigate(r.dir) })
                                DropdownMenuItem(text = { Text("Downloads") }, onClick = { rootsMenu = false; vm.navigate(Storage.downloads()) })
                            }
                        }
                        Box {
                            IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                            DropdownMenu(menu, { menu = false }) {
                                DropdownMenuItem(text = { Text("New folder") }, leadingIcon = { Icon(Icons.Outlined.CreateNewFolder, null) },
                                    onClick = { menu = false; vm.newFolder() })
                                DropdownMenuItem(text = { Text(if (vm.showHidden) "Hide hidden files" else "Show hidden files") },
                                    onClick = { menu = false; vm.showHidden = !vm.showHidden; vm.refresh() })
                                DropdownMenuItem(text = { Text("About") }, leadingIcon = { Icon(Icons.Outlined.Info, null) },
                                    onClick = { menu = false; onAbout() })
                            }
                        }
                    }
                },
            )
        },
        floatingActionButton = {
            if (selecting) ExtendedFloatingActionButton(
                onClick = { vm.createArchiveDialog() },
                icon = { Icon(Icons.Outlined.Archive, null) },
                text = { Text("Compress") },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            if (!vm.hasAccess) AccessCard(onRequestAccess)
            if (vm.files.isEmpty() && vm.hasAccess) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("This folder is empty", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            LazyColumn(contentPadding = PaddingValues(bottom = 96.dp)) {
                items(vm.files, key = { it.absolutePath }) { f ->
                    val isArchive = !f.isDirectory && Archives.looksLikeArchive(f.name)
                    val sub = if (f.isDirectory) (f.list()?.size?.let { "$it item(s) · " } ?: "") + formatDate(ctx, f.lastModified())
                    else Paths.humanSize(f.length()) + " · " + formatDate(ctx, f.lastModified())
                    EntryRow(
                        icon = iconFor(f.name, f.isDirectory), title = f.name, subtitle = sub,
                        selected = f in vm.selectedFiles, selecting = selecting, accent = isArchive,
                        onClick = { if (selecting) vm.toggle(f) else vm.openFile(f) },
                        onLongClick = { vm.toggle(f) },
                    )
                }
            }
        }
    }
}

@Composable
private fun AccessCard(onRequestAccess: () -> Unit) {
    Card(
        Modifier.padding(16.dp).fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Allow access to your files", fontWeight = FontWeight.SemiBold)
            Text("OpenRAR needs access to all files to browse folders, open archives and save extracted files. " +
                "Archives opened from other apps work without it.", style = MaterialTheme.typography.bodyMedium)
            Button(onClick = onRequestAccess) { Text("Allow access") }
        }
    }
}

// ==================================================================== archive browser
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ArchiveScreen(vm: MainViewModel, snackbar: SnackbarHostState) {
    val a = vm.archive ?: return
    val ctx = LocalContext.current
    val selecting = vm.selectedEntries.isNotEmpty()
    val nodes: List<ArchiveTree.Node> = a.tree.list(vm.folder)
    var menu by remember { mutableStateOf(false) }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = if (selecting) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
                ),
                navigationIcon = {
                    if (selecting) IconButton(onClick = { vm.selectedEntries.clear() }) { Icon(Icons.Default.Close, "Clear selection") }
                    else IconButton(onClick = { if (!vm.folderUp()) vm.closeArchive() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
                },
                title = {
                    if (selecting) Text("${vm.selectedEntries.size} selected")
                    else Column {
                        Text(a.displayName, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        val where = if (vm.folder.isEmpty()) "" else " · /${vm.folder}"
                        Text("${a.info.format} · ${a.info.files} files · ${Paths.humanSize(a.info.unpackedSize)}$where",
                            style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                },
                actions = {
                    if (selecting) {
                        IconButton(onClick = { vm.selectedEntries.clear(); vm.selectedEntries.addAll(nodes.map { it.path }) }) {
                            Icon(Icons.Outlined.SelectAll, "Select all")
                        }
                        IconButton(onClick = { vm.shareEntries(vm.selectedEntries.toList()) }) { Icon(Icons.Outlined.Share, "Share") }
                        if (a.reader.editable && !a.info.locked) {
                            if (vm.selectedEntries.size == 1) IconButton(onClick = { vm.renameEntry(vm.selectedEntries[0]) }) {
                                Icon(Icons.Outlined.DriveFileRenameOutline, "Rename")
                            }
                            IconButton(onClick = { vm.deleteEntries(vm.selectedEntries.toList()) }) { Icon(Icons.Outlined.Delete, "Delete") }
                        }
                    } else {
                        IconButton(onClick = { vm.testArchive() }) { Icon(Icons.Outlined.CheckCircle, "Test") }
                        IconButton(onClick = { vm.showInfo() }) { Icon(Icons.Outlined.Info, "Information") }
                        Box {
                            IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                            DropdownMenu(menu, { menu = false }) {
                                DropdownMenuItem(text = { Text("Extract all…") }, leadingIcon = { Icon(Icons.Outlined.Unarchive, null) },
                                    onClick = { menu = false; vm.extractDialog(null) })
                                DropdownMenuItem(text = { Text("Close archive") }, onClick = { menu = false; vm.closeArchive() })
                            }
                        }
                    }
                },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { vm.extractDialog() },
                icon = { Icon(Icons.Outlined.Unarchive, null) },
                text = { Text(if (selecting) "Extract selected" else "Extract") },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            if (a.info.comment.isNotEmpty() && vm.folder.isEmpty()) {
                Text(a.info.comment, Modifier.padding(horizontal = 16.dp, vertical = 8.dp).fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp)).padding(12.dp),
                    style = MaterialTheme.typography.bodySmall, maxLines = 6, overflow = TextOverflow.Ellipsis)
            }
            if (nodes.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Empty archive") }
            }
            LazyColumn(contentPadding = PaddingValues(bottom = 96.dp)) {
                items(nodes, key = { it.path }) { n ->
                    val sub = buildString {
                        if (n.isDirectory) append("${n.files} file(s) · ")
                        append(Paths.humanSize(n.size))
                        n.packedSize?.let { if (n.size > 0) append(" → ${Paths.humanSize(it)}") }
                        formatDate(ctx, n.modified).takeIf { it.isNotEmpty() }?.let { append(" · $it") }
                    }
                    EntryRow(
                        icon = iconFor(n.name, n.isDirectory), title = n.name, subtitle = sub,
                        selected = n.path in vm.selectedEntries, selecting = selecting,
                        onClick = {
                            when {
                                selecting -> vm.toggleEntry(n.path)
                                n.isDirectory -> vm.enterFolder(n.path)
                                else -> vm.viewEntry(n.path)
                            }
                        },
                        onLongClick = { vm.toggleEntry(n.path) },
                        trailing = {
                            Row {
                                if (n.encrypted) Icon(Icons.Default.Lock, "Encrypted", Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                                if (!n.isDirectory && !selecting) {
                                    Spacer(Modifier.size(4.dp))
                                    Icon(Icons.Outlined.Visibility, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        },
                    )
                    HorizontalDivider(Modifier.padding(start = 72.dp), color = MaterialTheme.colorScheme.surfaceVariant)
                }
            }
        }
    }
}
