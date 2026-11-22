package io.github.chaoszero6.openrar

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.chaoszero6.openrar.core.ArchiveEntry
import io.github.chaoszero6.openrar.core.ArchiveException
import io.github.chaoszero6.openrar.core.ArchiveInfo
import io.github.chaoszero6.openrar.core.ArchiveReader
import io.github.chaoszero6.openrar.core.ArchiveTree
import io.github.chaoszero6.openrar.core.ArchiveWriter
import io.github.chaoszero6.openrar.core.Archives
import io.github.chaoszero6.openrar.core.CancelledException
import io.github.chaoszero6.openrar.core.CreateOptions
import io.github.chaoszero6.openrar.core.ExtractOptions
import io.github.chaoszero6.openrar.core.OverwriteAnswer
import io.github.chaoszero6.openrar.core.OverwriteMode
import io.github.chaoszero6.openrar.core.PasswordRequiredException
import io.github.chaoszero6.openrar.core.Paths
import io.github.chaoszero6.openrar.core.Progress
import io.github.chaoszero6.openrar.core.WrongPasswordException
import io.github.chaoszero6.openrar.core.ZipEditor
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.Executors

class OpenArchive(
    val file: File,
    val displayName: String,
    val reader: ArchiveReader,
    val password: CharArray?,
    /** Where "Extract" goes by default (next to the archive, or Downloads for archives opened from other apps). */
    val extractBase: File,
) {
    val tree = ArchiveTree(reader.entries)
    val info: ArchiveInfo = reader.info()
}

class TaskState(val title: String, val determinate: Boolean = true) {
    var file by mutableStateOf("")
    var done by mutableLongStateOf(0L)
    var total by mutableLongStateOf(0L)
    @Volatile var cancelled = false
}

data class UiMessage(val text: String, val actionLabel: String? = null, val action: (() -> Unit)? = null)

sealed interface Dialog {
    data class Password(val name: String, val wrong: Boolean, val onResult: (CharArray?) -> Unit) : Dialog
    data class Overwrite(val target: File, val entry: ArchiveEntry, val answer: CompletableDeferred<OverwriteAnswer>) : Dialog
    data class Extract(val selection: List<String>?, val here: File, val subfolder: File, val useSubfolder: Boolean,
                       val stripPrefix: String) : Dialog
    data class Create(val sources: List<File>, val destDir: File, val defaultName: String) : Dialog
    data class Info(val name: String, val info: ArchiveInfo) : Dialog
    data class Message(val title: String, val text: String) : Dialog
    data class Confirm(val title: String, val text: String, val confirm: String, val onConfirm: () -> Unit) : Dialog
    data class Rename(val title: String, val current: String, val onRename: (String) -> Unit) : Dialog
}

class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val ctx get() = getApplication<Application>()
    private val worker = Executors.newSingleThreadExecutor().asCoroutineDispatcher()

    // ---------------------------------------------------------------- state
    var hasAccess by mutableStateOf(Storage.hasAccess(app))
    var dir by mutableStateOf(Storage.roots(app).first().dir)
    var files by mutableStateOf<List<File>>(emptyList())
    var showHidden by mutableStateOf(false)
    val selectedFiles = mutableStateListOf<File>()

    var archive by mutableStateOf<OpenArchive?>(null)
    var folder by mutableStateOf("")
    val selectedEntries = mutableStateListOf<String>()

    var task by mutableStateOf<TaskState?>(null)
    var dialog by mutableStateOf<Dialog?>(null)
    val messages = MutableSharedFlow<UiMessage>(extraBufferCapacity = 8)

    init {
        NativeRarReader.install()          // RAR5 / encrypted headers / volumes; junrar (RAR 4.x) otherwise
        refresh()
    }

    // ---------------------------------------------------------------- file browser
    fun onResume() {
        hasAccess = Storage.hasAccess(ctx)
        refresh()
    }

    fun refresh() {
        val list = dir.listFiles()?.toList().orEmpty()
            .filter { showHidden || !it.name.startsWith(".") }
            .sortedWith(compareBy<File>({ !it.isDirectory }, { it.name.lowercase() }))
        files = list
        selectedFiles.retainAll(list.toSet())
    }

    fun navigate(to: File) {
        dir = to
        selectedFiles.clear()
        refresh()
    }

    fun canGoUp(): Boolean {
        val roots = Storage.roots(ctx).map { it.dir.absolutePath }
        return dir.absolutePath !in roots && dir.parentFile != null
    }

    fun toggle(f: File) {
        if (f in selectedFiles) selectedFiles.remove(f) else selectedFiles.add(f)
    }

    fun openFile(f: File) {
        when {
            f.isDirectory -> navigate(f)
            Archives.looksLikeArchive(f.name) || isSfx(f) -> openArchive(f)
            else -> Storage.openExternally(ctx, f)
        }
    }

    private fun isSfx(f: File): Boolean = f.name.lowercase().let { it.endsWith(".run") || it.endsWith(".exe") } &&
        runCatching { Archives.sniff(f.inputStream().use { inp -> ByteArray(512).let { b -> b.copyOf(maxOf(0, inp.read(b))) } }) }.getOrNull() in
        setOf(Archives.Kind.LINUX_SFX, Archives.Kind.EXE)

    fun newFolder() {
        dialog = Dialog.Rename("New folder", "") { name ->
            val f = File(dir, name.trim())
            if (name.isBlank() || name.contains('/') || !f.mkdir()) toast("Cannot create folder \"$name\"") else refresh()
        }
    }

    fun deleteSelectedFiles() {
        val items = selectedFiles.toList()
        if (items.isEmpty()) return
        dialog = Dialog.Confirm("Delete ${items.size} item(s)?", items.joinToString("\n") { it.name }.take(600), "Delete") {
            runTask("Deleting") {
                items.forEach { it.deleteRecursively() }
                withContext(Dispatchers.Main) {
                    selectedFiles.clear()
                    refresh()
                }
            }
        }
    }

    fun renameFile(f: File) {
        dialog = Dialog.Rename("Rename", f.name) { name ->
            val t = File(f.parentFile, name.trim())
            if (name.isBlank() || name.contains('/') || t.exists() || !f.renameTo(t)) toast("Cannot rename to \"$name\"")
            selectedFiles.clear()
            refresh()
        }
    }

    fun createArchiveDialog(sources: List<File> = selectedFiles.toList(), destDir: File = dir) {
        if (sources.isEmpty()) return
        val base = if (sources.size == 1) Paths.stripArchiveExtension(sources[0].name).ifEmpty { sources[0].name }
        else (sources[0].parentFile?.name ?: "archive")
        dialog = Dialog.Create(sources, destDir, base)
    }

    fun create(sources: List<File>, dest: File, opts: CreateOptions, addToExisting: Boolean) {
        runTask("Creating ${dest.name}") { p ->
            val outs = if (addToExisting) {
                ZipEditor.add(dest, sources, "", opts.password, opts.level, p)
                listOf(dest)
            } else ArchiveWriter.create(dest, sources, opts, p)
            withContext(Dispatchers.Main) {
                selectedFiles.clear()
                if (dest.parentFile == dir) refresh() else navigate(dest.parentFile!!)
                val what = if (outs.size > 1) "${outs.size} volumes (${outs.first().name} …)" else outs.first().name
                messages.tryEmit(UiMessage("Created $what", "Open") { openArchive(outs.first()) })
            }
        }
    }

    // ---------------------------------------------------------------- archives
    fun openArchive(file: File, password: CharArray? = null, displayName: String = file.name, extractBase: File? = null) {
        runTask("Opening $displayName", determinate = false) { openNow(file, password, displayName, extractBase) }
    }

    /** Open inside an already running task; a password prompt restarts opening once answered. */
    private suspend fun openNow(file: File, password: CharArray?, displayName: String, extractBase: File?) {
        try {
            val r = Archives.open(file, password)
            val a = OpenArchive(file, displayName, r, password, extractBase ?: file.parentFile ?: Storage.downloads())
            withContext(Dispatchers.Main) {
                archive?.reader?.let { old -> closeReader(old) }
                archive = a
                folder = ""
                selectedEntries.clear()
            }
        } catch (e: PasswordRequiredException) {
            askPassword(displayName, false) { pw -> pw?.let { openArchive(file, it, displayName, extractBase) } }
        } catch (e: WrongPasswordException) {
            askPassword(displayName, true) { pw -> pw?.let { openArchive(file, it, displayName, extractBase) } }
        }
    }

    private fun closeReader(r: ArchiveReader) {
        viewModelScope.launch(worker) { runCatching { r.close() } }
    }

    fun closeArchive() {
        archive?.reader?.let { closeReader(it) }
        archive = null
        folder = ""
        selectedEntries.clear()
        refresh()
    }

    /** Re-open after the archive changed or a password became known. */
    private suspend fun reopen(a: OpenArchive, password: CharArray? = a.password): OpenArchive {
        runCatching { a.reader.close() }
        val n = OpenArchive(a.file, a.displayName, Archives.open(a.file, password), password, a.extractBase)
        withContext(Dispatchers.Main) {
            archive = n
            selectedEntries.retainAll(n.reader.entries.map { it.path }.toSet())
            if (!n.tree.isFolder(folder)) folder = ""
        }
        return n
    }

    fun enterFolder(path: String) {
        folder = path
        selectedEntries.clear()
    }

    fun folderUp(): Boolean {
        if (folder.isEmpty()) return false
        folder = folder.substringBeforeLast('/', "")
        selectedEntries.clear()
        return true
    }

    fun toggleEntry(path: String) {
        if (path in selectedEntries) selectedEntries.remove(path) else selectedEntries.add(path)
    }

    fun extractDialog(selection: List<String>? = selectedEntries.toList().ifEmpty { null }) {
        val a = archive ?: return
        val stem = Paths.stripArchiveExtension(a.displayName).ifEmpty { "extracted" }
        val chosen = selection ?: a.reader.entries.map { it.path }
        val tops = chosen.map { it.removePrefix(if (folder.isEmpty()) "" else "$folder/").substringBefore('/') }.toSet()
        dialog = Dialog.Extract(selection, a.extractBase, File(a.extractBase, stem), tops.size > 1,
            stripPrefix = if (selection != null && folder.isNotEmpty()) folder else "")
    }

    fun extract(selection: List<String>?, dest: File, mode: OverwriteMode, keepPaths: Boolean, stripPrefix: String) {
        val a0 = archive ?: return
        runTask("Extracting ${a0.displayName}") { p ->
            var a = a0
            while (true) {
                try {
                    val res = a.reader.extract(selection, ExtractOptions(dest, mode, keepPaths, stripPrefix), p, ::askOverwrite)
                    withContext(Dispatchers.Main) {
                        if (res.errors.isNotEmpty()) {
                            dialog = Dialog.Message("Extracted with errors", res.errors.joinToString("\n").take(3000))
                        } else {
                            val n = res.extracted.size
                            messages.tryEmit(UiMessage("Extracted $n file(s)" + (if (res.skipped.isNotEmpty()) ", skipped ${res.skipped.size}" else ""),
                                "Show") { closeArchive(); navigate(dest) })
                        }
                    }
                    return@runTask
                } catch (e: PasswordRequiredException) {
                    val pw = awaitPassword(a.displayName, false) ?: return@runTask
                    a = reopen(a, pw)
                } catch (e: WrongPasswordException) {
                    val pw = awaitPassword(a.displayName, true) ?: return@runTask
                    a = reopen(a, pw)
                }
            }
        }
    }

    /** Extract one entry to the cache and hand it to another app. */
    fun viewEntry(path: String) = extractToCache(listOf(path), "view") { files ->
        files.firstOrNull()?.let { Storage.openExternally(ctx, it) }
    }

    fun shareEntries(paths: List<String>) = extractToCache(paths, "share") { files ->
        if (files.isNotEmpty()) Storage.share(ctx, files)
    }

    private fun extractToCache(paths: List<String>, kind: String, then: (List<File>) -> Unit) {
        val a0 = archive ?: return
        runTask("Preparing ${paths.size} file(s)") { p ->
            val dest = File(ctx.cacheDir, "$kind/${SystemClock.elapsedRealtime()}")
            var a = a0
            while (true) {
                try {
                    val res = a.reader.extract(paths, ExtractOptions(dest, OverwriteMode.RENAME, keepPaths = false), p)
                    if (res.errors.isNotEmpty()) throw ArchiveException(res.errors.joinToString("\n"))
                    withContext(Dispatchers.Main) { then(res.extracted) }
                    return@runTask
                } catch (e: PasswordRequiredException) {
                    a = reopen(a, awaitPassword(a.displayName, false) ?: return@runTask)
                } catch (e: WrongPasswordException) {
                    a = reopen(a, awaitPassword(a.displayName, true) ?: return@runTask)
                }
            }
        }
    }

    fun testArchive() {
        val a0 = archive ?: return
        runTask("Testing ${a0.displayName}") { p ->
            var a = a0
            while (true) {
                try {
                    val errs = a.reader.test(p)
                    withContext(Dispatchers.Main) {
                        dialog = if (errs.isEmpty()) Dialog.Message("Test", "No errors found in ${a.displayName}.")
                        else Dialog.Message("Test found problems", errs.joinToString("\n").take(3000))
                    }
                    return@runTask
                } catch (e: PasswordRequiredException) {
                    a = reopen(a, awaitPassword(a.displayName, false) ?: return@runTask)
                } catch (e: WrongPasswordException) {
                    a = reopen(a, awaitPassword(a.displayName, true) ?: return@runTask)
                }
            }
        }
    }

    fun showInfo() {
        archive?.let { dialog = Dialog.Info(it.displayName, it.info) }
    }

    fun deleteEntries(paths: List<String>) {
        val a = archive ?: return
        dialog = Dialog.Confirm("Delete from archive?", paths.joinToString("\n") { it.substringAfterLast('/') }.take(600), "Delete") {
            runTask("Deleting from ${a.displayName}", determinate = false) {
                ZipEditor.delete(a.file, paths)
                reopen(a)
                withContext(Dispatchers.Main) { selectedEntries.clear() }
            }
        }
    }

    fun renameEntry(path: String) {
        val a = archive ?: return
        dialog = Dialog.Rename("Rename in archive", path.substringAfterLast('/')) { name ->
            runTask("Renaming", determinate = false) {
                ZipEditor.rename(a.file, path, name.trim())
                reopen(a)
                withContext(Dispatchers.Main) { selectedEntries.clear() }
            }
        }
    }

    // ---------------------------------------------------------------- intents from other apps
    fun handleIntent(intent: Intent?) {
        intent ?: return
        when (intent.action) {
            Intent.ACTION_VIEW -> intent.data?.let { uri -> openUri(uri) }
            Intent.ACTION_SEND, Intent.ACTION_SEND_MULTIPLE -> {
                val uris = streams(intent)
                if (uris.isNotEmpty()) importAndCompress(uris)
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun streams(intent: Intent): List<Uri> = if (intent.action == Intent.ACTION_SEND) {
        listOfNotNull(if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            else intent.getParcelableExtra(Intent.EXTRA_STREAM))
    } else {
        (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM)).orEmpty()
    }

    private fun openUri(uri: Uri) {
        runTask("Opening", determinate = false) {
            val name = Storage.displayName(ctx, uri)
            val f = Storage.copyToCache(ctx, uri, "incoming")
            val base = if (f.absolutePath.startsWith(ctx.cacheDir.absolutePath)) Storage.downloads() else f.parentFile!!
            openNow(f, null, name, base)
        }
    }

    private fun importAndCompress(uris: List<Uri>) {
        runTask("Receiving ${uris.size} file(s)", determinate = false) {
            val folder = "shared/${SystemClock.elapsedRealtime()}"
            val files = uris.map { Storage.copyToCache(ctx, it, folder) }
            withContext(Dispatchers.Main) { createArchiveDialog(files, Storage.downloads()) }
        }
    }

    // ---------------------------------------------------------------- tasks, questions
    private fun runTask(title: String, determinate: Boolean = true, block: suspend (Progress) -> Unit) {
        if (task != null) {
            toast("Please wait for the current operation to finish")
            return
        }
        val t = TaskState(title, determinate)
        task = t
        val progress = object : Progress {
            private var last = 0L
            private var done = 0L
            override fun total(bytes: Long) {
                t.total = bytes
                done = 0
                t.done = 0
            }

            override fun file(name: String) {
                t.file = name
            }

            override fun advance(bytes: Long) {
                done += bytes
                val now = SystemClock.uptimeMillis()
                if (now - last > 80) {
                    last = now
                    t.done = done
                }
            }

            override val isCancelled get() = t.cancelled
        }
        viewModelScope.launch(worker) {
            try {
                block(progress)
            } catch (e: CancelledException) {
                messages.tryEmit(UiMessage("Cancelled"))
            } catch (e: ArchiveException) {
                withContext(Dispatchers.Main) { dialog = Dialog.Message(title, e.message ?: e.toString()) }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) { dialog = Dialog.Message(title, "Error: ${e.message ?: e}") }
            } finally {
                withContext(Dispatchers.Main) {
                    if (task === t) task = null
                    refresh()
                }
            }
        }
    }

    fun cancelTask() {
        task?.cancelled = true
    }

    private fun askPassword(name: String, wrong: Boolean, then: (CharArray?) -> Unit) {
        viewModelScope.launch(Dispatchers.Main) { dialog = Dialog.Password(name, wrong) { then(it) } }
    }

    /** Ask for a password from the worker thread and wait for the answer. */
    private suspend fun awaitPassword(name: String, wrong: Boolean): CharArray? {
        val d = CompletableDeferred<CharArray?>()
        withContext(Dispatchers.Main) { dialog = Dialog.Password(name, wrong) { d.complete(it) } }
        return d.await()
    }

    /** Called by the extractor on the worker thread; blocks until the user answers. */
    private fun askOverwrite(target: File, entry: ArchiveEntry): OverwriteAnswer {
        val d = CompletableDeferred<OverwriteAnswer>()
        dialog = Dialog.Overwrite(target, entry, d)
        return runBlocking { d.await() }
    }

    fun dismissDialog() {
        when (val d = dialog) {
            is Dialog.Password -> d.onResult(null)
            is Dialog.Overwrite -> d.answer.complete(OverwriteAnswer.CANCEL)
            else -> Unit
        }
        dialog = null
    }

    fun toast(text: String) {
        messages.tryEmit(UiMessage(text))
    }

    override fun onCleared() {
        archive?.reader?.let { runCatching { it.close() } }
        worker.close()
    }
}
