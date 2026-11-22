package io.github.chaoszero6.openrar.core

import com.github.junrar.Archive
import com.github.junrar.exception.CrcErrorException
import com.github.junrar.exception.UnsupportedRarV5Exception
import net.lingala.zip4j.ZipFile
import net.lingala.zip4j.exception.ZipException
import net.lingala.zip4j.model.FileHeader
import org.apache.commons.compress.PasswordRequiredException as CommonsPasswordRequired
import org.apache.commons.compress.archivers.sevenz.SevenZFile
import org.apache.commons.compress.archivers.sevenz.SevenZMethod
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream
import org.apache.commons.compress.compressors.xz.XZCompressorInputStream
import java.io.BufferedInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FilterInputStream
import java.io.IOException
import java.io.InputStream
import java.io.RandomAccessFile
import java.nio.charset.StandardCharsets
import org.apache.commons.compress.archivers.zip.ZipFile as CommonsZipFile

// ================================================================== ZIP (zip4j: AES-256, ZipCrypto, SFX .exe)
class ZipReader(file: File, password: CharArray?) : ArchiveReader(file, password) {
    private val zip = ZipFile(file, password).apply { charset = StandardCharsets.UTF_8 }
    private val headers: List<FileHeader>

    init {
        try {
            headers = zip.fileHeaders
        } catch (e: ZipException) {
            zip.close()
            throw CorruptArchiveException("Not a valid ZIP archive: ${e.message}", e)
        }
    }

    override val selfExtracting: Boolean = file.inputStream().use { it.read() == 'M'.code }
    override val format: String = if (selfExtracting) "ZIP (SFX)" else "ZIP"
    override val editable: Boolean = true
    override val rawComment: String = try {
        zip.comment ?: ""
    } catch (_: Exception) {
        ""
    }
    override val entries: List<ArchiveEntry> = headers.map { h ->
        val attrs = h.externalFileAttributes
        val mode = if (attrs != null && attrs.size >= 4) (((attrs[3].toInt() and 0xff) shl 8) or (attrs[2].toInt() and 0xff)) and 0xfff else 0
        ArchiveEntry(
            path = Paths.normalize(h.fileName), isDirectory = h.isDirectory, size = h.uncompressedSize,
            packedSize = h.compressedSize, modified = h.lastModifiedTimeEpoch, crc = h.crc,
            encrypted = h.isEncrypted, unixMode = mode,
        )
    }

    override fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String> {
        val errors = mutableListOf<String>()
        for ((h, e) in headers.zip(entries)) {
            if (e.isDirectory || (selected != null && e.path !in selected)) continue
            if (e.encrypted && password == null) throw PasswordRequiredException()
            deliver(e, sink, progress, errors) { zip.getInputStream(h) }
        }
        return errors
    }

    override fun mapError(entry: ArchiveEntry, e: Exception): Exception = when {
        e is ZipException && e.type == ZipException.Type.WRONG_PASSWORD -> WrongPasswordException(cause = e)
        e is ZipException && e.type == ZipException.Type.CHECKSUM_MISMATCH -> IOException("CRC error, the file is damaged")
        e is ZipException && entry.encrypted && e.message?.contains("password", true) == true -> WrongPasswordException(cause = e)
        else -> e
    }

    override fun close() = zip.close()
}

// ================================================================== split ZIP (name.zip.001 ...), no AES
class SplitZipReader(file: File, password: CharArray?) : ArchiveReader(file, password) {
    private val channel = SplitFileChannel.read(Volumes.list(file))
    private val zip = try {
        CommonsZipFile.builder().setSeekableByteChannel(channel).get()
    } catch (e: IOException) {
        channel.close()
        throw CorruptArchiveException("Not a valid split ZIP archive: ${e.message}", e)
    }
    private val raw = zip.entries.toList()
    override val format = "ZIP"
    override val entries: List<ArchiveEntry> = raw.map { z ->
        ArchiveEntry(
            Paths.normalize(z.name), z.isDirectory, z.size, z.compressedSize, z.time.takeIf { it > 0 },
            z.crc.takeIf { it >= 0 }, z.generalPurposeBit.usesEncryption(), z.unixMode and 0xfff,
        )
    }

    override fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String> {
        val errors = mutableListOf<String>()
        for ((z, e) in raw.zip(entries)) {
            if (e.isDirectory || (selected != null && e.path !in selected)) continue
            if (e.encrypted) throw UnsupportedArchiveException("Encrypted split ZIP archives are not supported on Android. Join the volumes on a computer first.")
            deliver(e, sink, progress, errors) { zip.getInputStream(z) }
        }
        return errors
    }

    override fun close() = zip.close()
}

// ================================================================== 7z (commons-compress: AES-256, encrypted names)
class SevenZipReader(file: File, password: CharArray?, override val namesEncrypted: Boolean) : ArchiveReader(file, password) {
    private fun openFile(): SevenZFile {
        val b = SevenZFile.builder()
        if (Volumes.isVolume(file)) b.setSeekableByteChannel(SplitFileChannel.read(Volumes.list(file))) else b.setFile(file)
        if (password != null) b.setPassword(password)
        return b.get()
    }

    private var sz: SevenZFile = try {
        openFile()
    } catch (e: CommonsPasswordRequired) {
        throw PasswordRequiredException()
    } catch (e: IOException) {
        if (password != null && namesEncrypted) throw WrongPasswordException(cause = e)
        throw CorruptArchiveException("Not a valid 7z archive: ${e.message}", e)
    }

    override val format = "7Z"
    override val entries: List<ArchiveEntry> = sz.entries.map { z ->
        val enc = z.contentMethods?.any { it.method == SevenZMethod.AES256SHA256 } == true
        ArchiveEntry(
            Paths.normalize(z.name ?: ""), z.isDirectory, if (z.hasStream()) z.size else 0, null,
            if (z.hasLastModifiedDate) z.lastModifiedDate.time else null,
            if (z.hasCrc) z.crcValue else null, enc || namesEncrypted,
        )
    }.filter { it.path.isNotEmpty() }
    override val solid: Boolean = false

    override fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String> {
        val errors = mutableListOf<String>()
        sz.close()
        sz = openFile()                                    // sequential pass from the start
        var first = true
        while (true) {
            progress.check()
            val z = sz.nextEntry ?: break
            val e = ArchiveEntry(Paths.normalize(z.name ?: ""), z.isDirectory, z.size, encrypted = z.contentMethods?.any { it.method == SevenZMethod.AES256SHA256 } == true,
                modified = if (z.hasLastModifiedDate) z.lastModifiedDate.time else null)
            if (e.isDirectory || !z.hasStream() || (selected != null && e.path !in selected)) continue
            if (e.encrypted && password == null) throw PasswordRequiredException()
            val isFirst = first
            first = false
            deliver(e, sink, progress, errors) {
                object : InputStream() {
                    override fun read(): Int = sz.read()
                    override fun read(b: ByteArray, off: Int, len: Int): Int = try {
                        sz.read(b, off, len)
                    } catch (ex: IOException) {
                        // AES with a wrong password shows up as garbage for the decompressor or a CRC error
                        if (e.encrypted && isFirst && errors.isEmpty()) throw WrongPasswordException(cause = ex)
                        throw ex
                    }
                }
            }
        }
        return errors
    }

    override fun mapError(entry: ArchiveEntry, e: Exception): Exception =
        if (e.message?.contains("checksum", true) == true || e.message?.contains("crc", true) == true)
            IOException("CRC error, the file is damaged") else e

    override fun close() = sz.close()
}

// ================================================================== TAR (+ gz / bz2 / xz) and OpenRAR Linux SFX
class TarReader(file: File, private val compression: String, private val offset: Long = 0) : ArchiveReader(file, null) {
    override val format = "TAR" + (if (compression.isNotEmpty()) "." + compression.uppercase() else "") + (if (offset > 0) " (SFX)" else "")
    override val selfExtracting = offset > 0
    override val solid = compression.isNotEmpty()

    private fun open(): TarArchiveInputStream {
        val raw = if (Volumes.isVolume(file)) {
            java.nio.channels.Channels.newInputStream(SplitFileChannel.read(Volumes.list(file)))
        } else FileInputStream(file).also { skipFully(it, offset) }
        return TarArchiveInputStream(decompress(BufferedInputStream(raw, 256 * 1024), compression), "UTF-8")
    }

    override val entries: List<ArchiveEntry> by lazy {
        val out = mutableListOf<ArchiveEntry>()
        try {
            open().use { t ->
                while (true) {
                    val z = t.nextEntry ?: break
                    if (!(z.isFile || z.isDirectory)) continue
                    out.add(ArchiveEntry(Paths.normalize(z.name), z.isDirectory, if (z.isFile) z.size else 0, null,
                        z.modTime?.time, null, false, z.mode and 0xfff))
                }
            }
        } catch (e: IOException) {
            if (out.isEmpty()) throw CorruptArchiveException("Not a valid TAR archive: ${e.message}", e)
            out.add(ArchiveEntry("<archive is truncated or damaged>", false, 0))
        }
        out.filter { it.path.isNotEmpty() }
    }

    override fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String> {
        val errors = mutableListOf<String>()
        val left = selected?.toMutableSet()
        try {
            open().use { t ->
                while (true) {
                    progress.check()
                    val z = t.nextEntry ?: break
                    if (!z.isFile) continue
                    val e = ArchiveEntry(Paths.normalize(z.name), false, z.size, modified = z.modTime?.time, unixMode = z.mode and 0xfff)
                    if (left != null && e.path !in left) continue
                    left?.remove(e.path)
                    deliver(e, sink, progress, errors) { NonClosing(t) }
                    if (left != null && left.isEmpty()) break
                }
            }
        } catch (e: IOException) {
            errors.add("Archive data error: ${e.message}")
        }
        left?.filter { p -> entries.any { it.path == p && !it.isDirectory } }?.forEach { errors.add("$it: missing from archive data") }
        return errors
    }
}

// ================================================================== single .gz / .bz2 / .xz file
class SingleFileReader(file: File, private val compression: String) : ArchiveReader(file, null) {
    override val format = compression.uppercase()
    private val inner = file.name.let { n ->
        val low = n.lowercase()
        when {
            low.endsWith(".$compression") -> n.dropLast(compression.length + 1)
            low.endsWith(".tgz") -> n.dropLast(4) + ".tar"
            else -> "$n.out"
        }
    }
    override val entries: List<ArchiveEntry> = listOf(
        ArchiveEntry(inner, false, gzipSize(), file.length(), file.lastModified()),
    )

    private fun gzipSize(): Long {
        if (compression != "gz" || file.length() < 18) return 0
        RandomAccessFile(file, "r").use { r ->
            r.seek(r.length() - 4)
            val b = ByteArray(4)
            r.readFully(b)
            return (b[0].toLong() and 0xff) or ((b[1].toLong() and 0xff) shl 8) or
                ((b[2].toLong() and 0xff) shl 16) or ((b[3].toLong() and 0xff) shl 24)
        }
    }

    override fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String> {
        val errors = mutableListOf<String>()
        val e = entries.first()
        if (selected == null || e.path in selected) {
            deliver(e, sink, progress, errors) { decompress(BufferedInputStream(FileInputStream(file)), compression) }
        }
        return errors
    }
}

// ================================================================== RAR 1.5 - 4.x (junrar)
class RarReader(file: File, password: CharArray?) : ArchiveReader(file, password) {
    private val rar: Archive = try {
        if (password != null) Archive(file, String(password)) else Archive(file)
    } catch (e: UnsupportedRarV5Exception) {
        throw UnsupportedArchiveException(RAR5_MESSAGE)
    } catch (e: Exception) {
        if (password != null) throw WrongPasswordException(cause = e)
        if (e.message?.contains("encrypt", true) == true) throw PasswordRequiredException()
        throw CorruptArchiveException("Cannot open this RAR archive: ${e.message ?: e}", e)
    }

    init {
        if (rar.isEncrypted && password == null) {
            rar.close()
            throw PasswordRequiredException()
        }
    }

    private val headers = rar.fileHeaders
    override val format = "RAR"
    override val namesEncrypted: Boolean = rar.isEncrypted
    override val solid: Boolean = try {
        rar.mainHeader?.isSolid == true
    } catch (_: Exception) {
        false
    }
    override val entries: List<ArchiveEntry> = headers.map { h ->
        ArchiveEntry(Paths.normalize(h.fileName.replace('\\', '/')), h.isDirectory, h.fullUnpackSize, h.fullPackSize,
            h.mTime?.time, h.fileCRC.toLong() and 0xffffffffL, h.isEncrypted)
    }

    override fun stream(selected: Set<String>?, sink: EntrySink, progress: Progress): List<String> {
        val errors = mutableListOf<String>()
        for ((h, e) in headers.zip(entries)) {
            progress.check()
            if (e.isDirectory || (selected != null && e.path !in selected)) continue
            if (e.encrypted && password == null) throw PasswordRequiredException()
            val out = sink.open(e) ?: continue
            var error: String? = null
            try {
                rar.extractFile(h, ProgressOutputStream(out, progress))
            } catch (ex: CancelledException) {
                sink.close(e, out, "cancelled")
                throw ex
            } catch (ex: CrcErrorException) {
                error = if (e.encrypted) "wrong password or damaged file" else "CRC error, the file is damaged"
            } catch (ex: Exception) {
                error = ex.message ?: ex.toString()
            }
            sink.close(e, out, error)
            if (error != null) {
                if (e.encrypted && errors.isEmpty() && error.startsWith("wrong password")) throw WrongPasswordException()
                errors.add("${e.path}: $error")
            }
        }
        return errors
    }

    override fun close() = rar.close()

    companion object {
        const val RAR5_MESSAGE = "This is a RAR5 archive. The Android version can open RAR 4.x and older only; " +
            "RAR5 needs the native UnRAR code. Open it with OpenRAR on Windows or Linux, or convert it to 7Z there."
    }
}

// ================================================================== helpers
internal fun decompress(inp: InputStream, compression: String): InputStream = when (compression) {
    "gz" -> GzipCompressorInputStream(inp, true)
    "bz2" -> BZip2CompressorInputStream(inp, true)
    "xz" -> XZCompressorInputStream(inp, true)
    "" -> inp
    else -> throw UnsupportedArchiveException("$compression compression is not supported")
}

internal fun skipFully(inp: InputStream, n: Long) {
    var left = n
    while (left > 0) {
        val s = inp.skip(left)
        if (s <= 0) throw IOException("Unexpected end of file")
        left -= s
    }
}

private class NonClosing(inp: InputStream) : FilterInputStream(inp) {
    override fun close() {}
}
