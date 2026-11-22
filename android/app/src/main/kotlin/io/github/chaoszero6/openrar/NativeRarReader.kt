package io.github.chaoszero6.openrar

import io.github.chaoszero6.openrar.core.ArchiveEntry
import io.github.chaoszero6.openrar.core.ArchiveReader
import io.github.chaoszero6.openrar.core.CorruptArchiveException
import io.github.chaoszero6.openrar.core.EntrySink
import io.github.chaoszero6.openrar.core.PasswordRequiredException
import io.github.chaoszero6.openrar.core.Paths
import io.github.chaoszero6.openrar.core.Progress
import io.github.chaoszero6.openrar.core.WrongPasswordException
import net.sf.sevenzipjbinding.ExtractAskMode
import net.sf.sevenzipjbinding.ExtractOperationResult
import net.sf.sevenzipjbinding.IArchiveExtractCallback
import net.sf.sevenzipjbinding.IArchiveOpenCallback
import net.sf.sevenzipjbinding.IArchiveOpenVolumeCallback
import net.sf.sevenzipjbinding.ICryptoGetTextPassword
import net.sf.sevenzipjbinding.IInArchive
import net.sf.sevenzipjbinding.IInStream
import net.sf.sevenzipjbinding.ISequentialOutStream
import net.sf.sevenzipjbinding.PropID
import net.sf.sevenzipjbinding.SevenZip
import net.sf.sevenzipjbinding.SevenZipException
import net.sf.sevenzipjbinding.impl.RandomAccessFileInStream
import java.io.File
import java.io.OutputStream
import java.io.RandomAccessFile
import java.util.Date

/**
 * RAR 1.5 - 5.x through 7-Zip's native decoder (7-Zip-JBinding-4Android): solid archives, encrypted file
 * names, multi-volume sets (name.part1.rar ...) and RAR self-extracting .exe files. Extraction only.
 */
class NativeRarReader(file: File, password: CharArray?) : ArchiveReader(firstVolume(file), password) {
    private val streams = HashMap<String, RandomAccessFile>()
    private var askedForPassword = false

    /** Open callback: supplies other volumes and the header password. */
    private inner class OpenCallback : IArchiveOpenCallback, IArchiveOpenVolumeCallback, ICryptoGetTextPassword {
        private var current = this@NativeRarReader.file.absolutePath
        override fun setTotal(files: Long?, bytes: Long?) {}
        override fun setCompleted(files: Long?, bytes: Long?) {}
        override fun getProperty(propID: PropID?): Any? = if (propID == PropID.NAME) current else null
        override fun getStream(filename: String): IInStream? {
            val f = File(filename).let { if (it.isAbsolute) it else File(this@NativeRarReader.file.parentFile, filename) }
            if (!f.isFile) return null
            current = f.absolutePath
            return RandomAccessFileInStream(streams.getOrPut(f.absolutePath) { RandomAccessFile(f, "r") })
        }

        override fun cryptoGetTextPassword(): String {
            askedForPassword = true
            return password?.let { String(it) } ?: throw SevenZipException("password required")
        }
    }

    private val archive: IInArchive = try {
        val raf = RandomAccessFile(this.file, "r").also { streams[this.file.absolutePath] = it }
        SevenZip.openInArchive(null, RandomAccessFileInStream(raf), OpenCallback())
    } catch (e: SevenZipException) {
        closeStreams()
        when {
            askedForPassword && password == null -> throw PasswordRequiredException()
            askedForPassword -> throw WrongPasswordException(cause = e)
            else -> throw CorruptArchiveException("Cannot open this RAR archive: ${e.message}", e)
        }
    }

    override val format: String = "RAR"
    override val namesEncrypted: Boolean = askedForPassword
    override val selfExtracting: Boolean = this.file.inputStream().use { it.read() == 'M'.code }
    override val solid: Boolean = runCatching { archive.getArchiveProperty(PropID.SOLID) as? Boolean }.getOrNull() == true

    override val entries: List<ArchiveEntry> = (0 until archive.numberOfItems).map { i ->
        ArchiveEntry(
            path = Paths.normalize((archive.getProperty(i, PropID.PATH) as? String).orEmpty()),
            isDirectory = archive.getProperty(i, PropID.IS_FOLDER) as? Boolean == true,
            size = (archive.getProperty(i, PropID.SIZE) as? Long) ?: 0L,
            packedSize = archive.getProperty(i, PropID.PACKED_SIZE) as? Long,
            modified = (archive.getProperty(i, PropID.LAST_MODIFICATION_TIME) as? Date)?.time,
            crc = (archive.getProperty(i, PropID.CRC) as? Int)?.toLong()?.and(0xffffffffL),
            encrypted = archive.getProperty(i, PropID.ENCRYPTED) as? Boolean == true || askedForPassword,
        )
    }

    override fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String> {
        val indices = entries.indices.filter { i ->
            val e = entries[i]
            !e.isDirectory && (selected == null || e.path in selected)
        }
        if (indices.isEmpty()) return emptyList()
        if (password == null && indices.any { entries[it].encrypted }) throw PasswordRequiredException()
        val errors = mutableListOf<String>()
        var failure: Throwable? = null
        val cb = object : IArchiveExtractCallback, ICryptoGetTextPassword {
            var entry: ArchiveEntry? = null
            var out: OutputStream? = null
            var done = 0L

            override fun setTotal(total: Long) {}
            override fun setCompleted(complete: Long) {}

            override fun getStream(index: Int, mode: ExtractAskMode): ISequentialOutStream? {
                if (mode != ExtractAskMode.EXTRACT) return null
                val e = entries[index]
                entry = e
                out = try {
                    progress.check()
                    sink.open(e)
                } catch (t: Throwable) {
                    failure = t
                    throw SevenZipException("stopped")
                }
                val o = out ?: return null
                return ISequentialOutStream { data ->
                    try {
                        progress.check()
                        o.write(data)
                        progress.advance(data.size.toLong())
                    } catch (t: Throwable) {
                        failure = t
                        throw SevenZipException("stopped")
                    }
                    data.size
                }
            }

            override fun prepareOperation(mode: ExtractAskMode) {}

            override fun setOperationResult(result: ExtractOperationResult) {
                val e = entry ?: return
                val error = when (result) {
                    ExtractOperationResult.OK -> null
                    ExtractOperationResult.CRCERROR -> if (e.encrypted) "wrong password or damaged file" else "CRC error, the file is damaged"
                    ExtractOperationResult.WRONG_PASSWORD -> "wrong password"
                    ExtractOperationResult.DATAERROR -> if (e.encrypted) "wrong password or damaged data" else "data error, the file is damaged"
                    ExtractOperationResult.UNSUPPORTEDMETHOD -> "unsupported compression method"
                    else -> result.toString().lowercase().replace('_', ' ')
                }
                sink.close(e, out, error)
                if (error != null) errors.add("${e.path}: $error")
                entry = null
                out = null
            }

            override fun cryptoGetTextPassword(): String = password?.let { String(it) } ?: ""
        }
        try {
            archive.extract(indices.toIntArray(), false, cb)
        } catch (e: SevenZipException) {
            cb.entry?.let { sink.close(it, cb.out, "stopped") }
            failure?.let { throw it }
            errors.add("Archive data error: ${e.message}")
        }
        failure?.let { throw it }
        // wrong password: every encrypted file fails; report it as such instead of a list of CRC errors
        if (password != null && errors.isNotEmpty() && errors.size == indices.size &&
            indices.all { entries[it].encrypted } && errors.all { it.contains("password") }) {
            throw WrongPasswordException()
        }
        return errors
    }

    private fun closeStreams() {
        streams.values.forEach { runCatching { it.close() } }
        streams.clear()
    }

    override fun close() {
        runCatching { archive.close() }
        closeStreams()
    }

    companion object {
        private val PART = Regex("(?i)^(.*)\\.part(\\d+)\\.rar$")

        /** name.part3.rar -> name.part1.rar (or part01 / part001) when it exists. */
        fun firstVolume(f: File): File {
            val m = PART.matchEntire(f.name) ?: return f
            val digits = m.groupValues[2].length
            val first = File(f.parentFile, "${m.groupValues[1]}.part${"1".padStart(digits, '0')}.rar")
            return if (first.isFile) first else f
        }

        /** Register with the shared engine; false when the native library is not available on this device. */
        fun install(): Boolean = try {
            SevenZip.getSevenZipVersion()
            io.github.chaoszero6.openrar.core.Archives.nativeRar = { f, pw -> NativeRarReader(f, pw) }
            true
        } catch (t: Throwable) {
            false
        }
    }
}
