package io.github.chaoszero6.openrar.core

import java.io.Closeable
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream

/** Receives extracted entries. open() returns null to skip an entry. */
interface EntrySink {
    fun open(entry: ArchiveEntry): OutputStream?
    fun close(entry: ArchiveEntry, out: OutputStream?, error: String?)
}

/** An opened archive. Not thread-safe: use from one worker thread. */
abstract class ArchiveReader(val file: File, protected val password: CharArray?) : Closeable {
    abstract val format: String
    abstract val entries: List<ArchiveEntry>
    open val solid: Boolean = false
    open val namesEncrypted: Boolean = false
    open val rawComment: String = ""
    open val selfExtracting: Boolean = false
    /** Supports delete / rename / add (ZIP). */
    open val editable: Boolean = false
    val volumes: List<File> by lazy { if (Volumes.isVolume(file)) Volumes.list(file) else listOf(file) }

    val comment: String get() = Comments.visible(rawComment)
    val locked: Boolean get() = Comments.isLocked(rawComment)

    /** Stream the selected entries (null = all files) into the sink, in archive order. Returns errors. */
    protected abstract fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String>

    /** Entries under the given paths (folders include everything inside them). */
    fun expand(paths: Collection<String>): Set<String> {
        val want = paths.map { Paths.normalize(it) }
        return entries.filter { e -> want.any { w -> e.path == w || e.path.startsWith("$w/") } }
            .map { it.path }.toSet()
    }

    fun extract(
        selection: Collection<String>?,
        options: ExtractOptions,
        progress: Progress = Progress.NONE,
        ask: ((File, ArchiveEntry) -> OverwriteAnswer)? = null,
    ): ExtractResult {
        val chosen = selection?.let { expand(it) }
        val list = entries.filter { chosen == null || it.path in chosen }
        progress.total(list.filter { !it.isDirectory }.sumOf { it.size })
        val sink = DiskSink(options, ask, progress)
        for (e in list.filter { it.isDirectory }) sink.makeDir(e)
        val errors = stream(chosen, sink, progress)
        sink.finish()
        return ExtractResult(sink.extracted, sink.skipped, errors)
    }

    /** Decompress everything and verify checksums. Returns a list of problems (empty = OK). */
    fun test(progress: Progress = Progress.NONE): List<String> {
        progress.total(entries.filter { !it.isDirectory }.sumOf { it.size })
        val sink = object : EntrySink {
            override fun open(entry: ArchiveEntry): OutputStream {
                progress.file(entry.path)
                return ProgressOutputStream(NullOutputStream, progress)
            }

            override fun close(entry: ArchiveEntry, out: OutputStream?, error: String?) {}
        }
        return stream(null, sink, progress)
    }

    fun info(): ArchiveInfo {
        val es = entries
        val disk = volumes.sumOf { it.length() }
        val packed = es.sumOf { it.packedSize ?: 0 }
        return ArchiveInfo(
            format = format, files = es.count { !it.isDirectory }, folders = es.count { it.isDirectory },
            unpackedSize = es.filter { !it.isDirectory }.sumOf { it.size },
            packedSize = if (packed > 0) packed else disk,
            encrypted = es.any { it.encrypted } || namesEncrypted, namesEncrypted = namesEncrypted,
            solid = solid, volumes = volumes.size, comment = comment, locked = locked,
            selfExtracting = selfExtracting,
        )
    }

    /** Copy one entry's data from `input` into the sink, with progress; records errors per file. */
    protected fun deliver(entry: ArchiveEntry, sink: EntrySink, progress: Progress, errors: MutableList<String>,
                          input: () -> InputStream) {
        progress.check()
        val out = sink.open(entry) ?: return
        var error: String? = null
        try {
            input().use { inp -> copy(inp, out, progress) }
        } catch (e: CancelledException) {
            sink.close(entry, out, "cancelled")
            throw e
        } catch (e: WrongPasswordException) {
            sink.close(entry, out, e.message)
            throw e
        } catch (e: Exception) {
            val mapped = mapError(entry, e)
            if (mapped is WrongPasswordException) {
                sink.close(entry, out, mapped.message)
                throw mapped
            }
            error = mapped.message ?: e.toString()
        }
        sink.close(entry, out, error)
        if (error != null) errors.add("${entry.path}: $error")
    }

    /** Format-specific translation of library exceptions (wrong password, CRC errors). */
    protected open fun mapError(entry: ArchiveEntry, e: Exception): Exception = e

    override fun close() {}

    companion object {
        fun copy(inp: InputStream, out: OutputStream, progress: Progress) {
            val buf = ByteArray(256 * 1024)
            while (true) {
                progress.check()
                val n = inp.read(buf)
                if (n < 0) break
                out.write(buf, 0, n)
                progress.advance(n.toLong())
            }
        }
    }
}

object NullOutputStream : OutputStream() {
    override fun write(b: Int) {}
    override fun write(b: ByteArray, off: Int, len: Int) {}
}

/** Writes through and reports bytes to progress (used by readers that push data, like junrar). */
class ProgressOutputStream(private val out: OutputStream, private val progress: Progress) : OutputStream() {
    override fun write(b: Int) {
        out.write(b)
        progress.advance(1)
    }

    override fun write(b: ByteArray, off: Int, len: Int) {
        progress.check()
        out.write(b, off, len)
        progress.advance(len.toLong())
    }

    override fun flush() = out.flush()
    override fun close() = out.close()
}

/** Writes entries below a destination folder, honouring paths, overwrite mode and timestamps. */
class DiskSink(
    private val opts: ExtractOptions,
    private val ask: ((File, ArchiveEntry) -> OverwriteAnswer)?,
    private val progress: Progress,
) : EntrySink {
    val extracted = mutableListOf<File>()
    val skipped = mutableListOf<String>()
    private var mode = opts.overwrite
    private val targets = HashMap<OutputStream, File>()
    private val dirTimes = mutableListOf<Pair<File, Long>>()

    init {
        opts.destination.mkdirs()
        if (!opts.destination.isDirectory) throw IOException("Cannot create ${opts.destination}")
    }

    fun target(entry: ArchiveEntry): File? {
        var name = entry.path
        val prefix = Paths.normalize(opts.stripPrefix)
        if (prefix.isNotEmpty()) {
            if (!name.startsWith("$prefix/")) return null
            name = name.removePrefix("$prefix/")
        }
        if (!opts.keepPaths) name = name.substringAfterLast('/')
        val rel = Paths.safeRelative(name) ?: return null
        return File(opts.destination, rel)
    }

    fun makeDir(entry: ArchiveEntry) {
        if (!opts.keepPaths) return
        val f = target(entry) ?: return
        f.mkdirs()
        entry.modified?.let { dirTimes.add(f to it) }
    }

    private fun resolve(f: File, entry: ArchiveEntry): File? {
        if (!f.exists()) return f
        when (mode) {
            OverwriteMode.OVERWRITE -> return f
            OverwriteMode.SKIP -> return null
            OverwriteMode.RENAME -> return Paths.uniqueFile(f)
            OverwriteMode.ASK -> Unit
        }
        return when (ask?.invoke(f, entry) ?: OverwriteAnswer.YES) {
            OverwriteAnswer.YES -> f
            OverwriteAnswer.YES_ALL -> f.also { mode = OverwriteMode.OVERWRITE }
            OverwriteAnswer.NO -> null
            OverwriteAnswer.NO_ALL -> null.also { mode = OverwriteMode.SKIP }
            OverwriteAnswer.RENAME -> Paths.uniqueFile(f)
            OverwriteAnswer.CANCEL -> throw CancelledException()
        }
    }

    override fun open(entry: ArchiveEntry): OutputStream? {
        progress.file(entry.path)
        val t = target(entry) ?: return null
        val f = resolve(t, entry)
        if (f == null) {
            skipped.add(entry.path)
            progress.advance(entry.size)
            return null
        }
        f.parentFile?.mkdirs()
        if (f.isDirectory) f.deleteRecursively()
        val out = FileOutputStream(f).buffered(256 * 1024)
        targets[out] = f
        return out
    }

    override fun close(entry: ArchiveEntry, out: OutputStream?, error: String?) {
        if (out == null) return
        val f = targets.remove(out)
        try {
            out.close()
        } catch (_: IOException) {
        }
        if (f == null) return
        if (error != null) {
            f.delete()
            return
        }
        entry.modified?.let { f.setLastModified(it) }
        extracted.add(f)
    }

    fun finish() {
        for ((f, t) in dirTimes.sortedByDescending { it.first.path.length }) f.setLastModified(t)
    }
}

/** OpenRAR desktop keeps a lock flag and SFX settings inside the ZIP comment; hide them from users. */
object Comments {
    private const val LOCK = "[OpenRAR:locked]"
    private val SFX = Regex(";OpenRAR-SFX-begin.*?;OpenRAR-SFX-end", RegexOption.DOT_MATCHES_ALL)

    fun isLocked(raw: String) = raw.lines().any { it.trim() == LOCK }

    fun visible(raw: String): String =
        raw.replace(SFX, "").lines().filter { it.trim() != LOCK }.joinToString("\n").trim()
}
