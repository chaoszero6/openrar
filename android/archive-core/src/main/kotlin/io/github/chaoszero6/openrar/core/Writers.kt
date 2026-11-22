package io.github.chaoszero6.openrar.core

import net.lingala.zip4j.ZipFile
import net.lingala.zip4j.io.outputstream.ZipOutputStream
import net.lingala.zip4j.model.ZipParameters
import net.lingala.zip4j.model.enums.AesKeyStrength
import net.lingala.zip4j.model.enums.AesVersion
import net.lingala.zip4j.model.enums.CompressionLevel
import net.lingala.zip4j.model.enums.CompressionMethod
import net.lingala.zip4j.model.enums.EncryptionMethod
import org.apache.commons.compress.archivers.sevenz.SevenZMethod
import org.apache.commons.compress.archivers.sevenz.SevenZMethodConfiguration
import org.apache.commons.compress.archivers.sevenz.SevenZOutputFile
import org.apache.commons.compress.archivers.tar.TarArchiveEntry
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorOutputStream
import org.apache.commons.compress.compressors.gzip.GzipCompressorOutputStream
import org.apache.commons.compress.compressors.gzip.GzipParameters
import org.apache.commons.compress.compressors.xz.XZCompressorOutputStream
import org.tukaani.xz.LZMA2Options
import java.io.File
import java.io.FileInputStream
import java.io.OutputStream
import java.nio.channels.Channels
import java.nio.channels.FileChannel
import java.nio.channels.SeekableByteChannel
import java.nio.file.StandardOpenOption

/** A file or folder to add, with its name inside the archive. */
data class SourceItem(val file: File, val name: String, val isDirectory: Boolean)

object ArchiveWriter {
    /** Folders are added with their contents; names are relative to each source's parent folder. */
    fun gather(sources: List<File>): List<SourceItem> {
        val out = mutableListOf<SourceItem>()
        val seen = HashSet<String>()
        fun add(f: File, name: String) {
            if (!seen.add(name)) return
            if (f.isDirectory) {
                out.add(SourceItem(f, name, true))
                f.listFiles()?.sortedBy { it.name.lowercase() }?.forEach { c ->
                    if (!java.nio.file.Files.isSymbolicLink(c.toPath())) add(c, "$name/${c.name}")
                }
            } else if (f.isFile) {
                out.add(SourceItem(f, name, false))
            }
        }
        for (s in sources) add(s.absoluteFile, s.absoluteFile.name)
        return out
    }

    /** Create a new archive (replacing `dest`). Returns the written file(s): one, or the .001, .002 ... volumes. */
    fun create(dest: File, sources: List<File>, opts: CreateOptions, progress: Progress = Progress.NONE): List<File> {
        if (opts.password != null && !opts.format.canEncrypt) {
            throw UnsupportedArchiveException("${opts.format.label} archives can't be encrypted on Android. Use ZIP for a password.")
        }
        val items = gather(sources).filter { it.file.absoluteFile != dest.absoluteFile }
        if (items.isEmpty()) throw ArchiveException("Nothing to add")
        progress.total(items.filter { !it.isDirectory }.sumOf { it.file.length() })
        dest.parentFile?.mkdirs()
        val tmp = File(dest.parentFile, dest.name + ".openrar-tmp")
        val split = if (opts.volumeSize > 0) SplitFileChannel.create(dest, opts.volumeSize) else null
        val channel: SeekableByteChannel = split ?: FileChannel.open(
            tmp.toPath(), StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE,
            StandardOpenOption.READ,
        )
        var ok = false
        try {
            when (opts.format) {
                Format.ZIP -> writeZip(channel, items, opts, progress)
                Format.SEVEN_ZIP -> write7z(channel, items, opts, progress)
                else -> writeTar(channel, items, opts, progress)
            }
            ok = true
        } finally {
            if (channel.isOpen) channel.close()
            if (!ok) {
                tmp.delete()
                split?.paths?.forEach { it.delete() }
            }
        }
        val outputs = if (split != null) split.paths else {
            if (dest.exists()) dest.delete()
            if (!tmp.renameTo(dest)) throw ArchiveException("Cannot write ${dest.name}")
            listOf(dest)
        }
        if (opts.testAfter) {
            progress.file("Testing archive")
            val errs = Archives.open(outputs.first(), opts.password).use { it.test(progress) }
            if (errs.isNotEmpty()) throw ArchiveException("Test after archiving failed; source files were kept.\n" + errs.joinToString("\n"))
        }
        if (opts.deleteSources) sources.forEach { if (it.absoluteFile != dest.absoluteFile) it.deleteRecursively() }
        return outputs
    }

    private fun copyFile(f: File, out: OutputStream, progress: Progress) {
        FileInputStream(f).use { ArchiveReader.copy(it, out, progress) }
    }

    private fun writeZip(ch: SeekableByteChannel, items: List<SourceItem>, opts: CreateOptions, progress: Progress) {
        val level = when (opts.level) {
            1 -> CompressionLevel.FASTEST
            2 -> CompressionLevel.FAST
            3 -> CompressionLevel.NORMAL
            4 -> CompressionLevel.MAXIMUM
            else -> CompressionLevel.ULTRA
        }
        val out = Channels.newOutputStream(ch).buffered(256 * 1024)
        ZipOutputStream(out, opts.password).use { zos ->
            for (it in items) {
                progress.check()
                progress.file(it.name)
                val p = ZipParameters().apply {
                    fileNameInZip = if (it.isDirectory) it.name + "/" else it.name
                    lastModifiedFileTime = it.file.lastModified()
                    if (it.isDirectory || opts.level == 0) {
                        compressionMethod = CompressionMethod.STORE
                        entrySize = if (it.isDirectory) 0 else it.file.length()
                    } else {
                        compressionMethod = CompressionMethod.DEFLATE
                        compressionLevel = level
                    }
                    if (opts.password != null && !it.isDirectory) {
                        isEncryptFiles = true
                        encryptionMethod = EncryptionMethod.AES
                        aesKeyStrength = AesKeyStrength.KEY_STRENGTH_256
                        aesVersion = AesVersion.TWO
                    }
                }
                zos.putNextEntry(p)
                if (!it.isDirectory) copyFile(it.file, zos, progress)
                zos.closeEntry()
            }
        }
    }

    /** LZMA2 presets above 6 need 300+ MB of RAM to compress; phones get at most preset 6 (~100 MB). */
    private fun lzmaPreset(level: Int) = when (level) {
        1 -> 1
        2 -> 3
        3 -> 5
        else -> 6
    }

    private fun write7z(ch: SeekableByteChannel, items: List<SourceItem>, opts: CreateOptions, progress: Progress) {
        SevenZOutputFile(ch).use { sz ->
            sz.setContentMethods(
                listOf(
                    if (opts.level == 0) SevenZMethodConfiguration(SevenZMethod.COPY)
                    else SevenZMethodConfiguration(SevenZMethod.LZMA2, LZMA2Options(lzmaPreset(opts.level))),
                ),
            )
            val out = object : OutputStream() {
                override fun write(b: Int) = sz.write(b)
                override fun write(b: ByteArray, off: Int, len: Int) = sz.write(b, off, len)
            }
            for (it in items) {
                progress.check()
                progress.file(it.name)
                val e = sz.createArchiveEntry(it.file, it.name)
                // Windows attributes: some readers (py7zr) only recognise folders by FILE_ATTRIBUTE_DIRECTORY
                e.setHasWindowsAttributes(true)
                e.windowsAttributes = if (it.isDirectory) 0x10 else 0x20
                sz.putArchiveEntry(e)
                if (!it.isDirectory) copyFile(it.file, out, progress)
                sz.closeArchiveEntry()
            }
            sz.finish()
        }
    }

    private fun writeTar(ch: SeekableByteChannel, items: List<SourceItem>, opts: CreateOptions, progress: Progress) {
        val raw = Channels.newOutputStream(ch).buffered(256 * 1024)
        val lvl = opts.level.coerceIn(1, 5)
        val comp: OutputStream = when (opts.format) {
            Format.TAR_GZ -> GzipCompressorOutputStream(raw, GzipParameters().apply { compressionLevel = listOf(1, 3, 6, 8, 9)[lvl - 1] })
            Format.TAR_BZ2 -> BZip2CompressorOutputStream(raw, listOf(1, 3, 6, 8, 9)[lvl - 1])
            Format.TAR_XZ -> XZCompressorOutputStream(raw, lzmaPreset(lvl))
            else -> raw
        }
        TarArchiveOutputStream(comp, "UTF-8").use { tar ->
            tar.setLongFileMode(TarArchiveOutputStream.LONGFILE_POSIX)
            tar.setBigNumberMode(TarArchiveOutputStream.BIGNUMBER_POSIX)
            tar.setAddPaxHeadersForNonAsciiNames(true)
            for (it in items) {
                progress.check()
                progress.file(it.name)
                val e = TarArchiveEntry(it.file, if (it.isDirectory) it.name + "/" else it.name)
                tar.putArchiveEntry(e)
                if (!it.isDirectory) copyFile(it.file, tar, progress)
                tar.closeArchiveEntry()
            }
            tar.finish()
        }
    }
}

/** Changes to existing ZIP archives (zip4j copies untouched entries as they are, no password needed). */
object ZipEditor {
    private fun open(archive: File, password: CharArray?): ZipFile {
        Archives.open(archive, password).use { r ->
            if (!r.editable) throw UnsupportedArchiveException("Only ZIP archives can be changed on Android. ${r.format} archives are read-only here.")
            if (Volumes.isVolume(archive)) throw UnsupportedArchiveException("Split archives cannot be changed.")
            if (r.locked) throw LockedArchiveException()
        }
        return ZipFile(archive, password).apply { charset = Charsets.UTF_8 }
    }

    private fun headersUnder(zip: ZipFile, paths: Collection<String>): List<String> {
        val want = paths.map { Paths.normalize(it) }
        return zip.fileHeaders.map { it.fileName }.filter { n ->
            val p = Paths.normalize(n)
            want.any { p == it || p.startsWith("$it/") }
        }
    }

    fun delete(archive: File, paths: Collection<String>) {
        open(archive, null).use { zip ->
            val names = headersUnder(zip, paths)
            if (names.isNotEmpty()) zip.removeFiles(names)
        }
    }

    fun rename(archive: File, path: String, newName: String) {
        require(newName.isNotBlank() && !newName.contains('/')) { "Invalid name" }
        val old = Paths.normalize(path)
        val target = (if (old.contains('/')) old.substringBeforeLast('/') + "/" else "") + newName
        open(archive, null).use { zip ->
            val map = LinkedHashMap<String, String>()
            for (n in headersUnder(zip, listOf(old))) {
                val p = Paths.normalize(n)
                map[n] = target + p.removePrefix(old) + (if (n.endsWith("/")) "/" else "")
            }
            if (map.isNotEmpty()) zip.renameFiles(map)
        }
    }

    /** Add files or folders under `folder` ("" = archive root). Encrypts them when a password is given. */
    fun add(archive: File, sources: List<File>, folder: String = "", password: CharArray? = null, level: Int = 3,
            progress: Progress = Progress.NONE) {
        val items = ArchiveWriter.gather(sources)
        progress.total(items.filter { !it.isDirectory }.sumOf { it.file.length() })
        open(archive, password).use { zip ->
            val prefix = Paths.normalize(folder).let { if (it.isEmpty()) "" else "$it/" }
            for (it in items) {
                progress.check()
                progress.file(it.name)
                val p = ZipParameters().apply {
                    fileNameInZip = prefix + it.name + if (it.isDirectory) "/" else ""
                    compressionMethod = if (level == 0 || it.isDirectory) CompressionMethod.STORE else CompressionMethod.DEFLATE
                    if (password != null && !it.isDirectory) {
                        isEncryptFiles = true
                        encryptionMethod = EncryptionMethod.AES
                        aesKeyStrength = AesKeyStrength.KEY_STRENGTH_256
                    }
                }
                if (it.isDirectory) {
                    zip.addStream(java.io.ByteArrayInputStream(ByteArray(0)), p)
                } else {
                    p.lastModifiedFileTime = it.file.lastModified()
                    FileInputStream(it.file).use { inp -> zip.addStream(inp, p) }
                    progress.advance(it.file.length())
                }
            }
        }
    }
}

/** Repack any readable archive into another format through a temporary folder. */
object Converter {
    fun convert(src: File, dest: File, opts: CreateOptions, password: CharArray?, tempDir: File,
                progress: Progress = Progress.NONE): List<File> {
        val stage = File(tempDir, "convert-" + System.nanoTime()).apply { mkdirs() }
        try {
            val errors = Archives.open(src, password).use {
                it.extract(null, ExtractOptions(stage, OverwriteMode.OVERWRITE), progress).errors
            }
            if (errors.isNotEmpty()) throw ArchiveException("The source archive has errors:\n" + errors.joinToString("\n"))
            val items = stage.listFiles()?.sortedBy { it.name }?.toList().orEmpty()
            return ArchiveWriter.create(dest, items, opts.copy(deleteSources = false), progress)
        } finally {
            stage.deleteRecursively()
        }
    }
}
