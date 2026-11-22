package io.github.chaoszero6.openrar.core

import org.apache.commons.compress.archivers.sevenz.SevenZFile
import java.io.BufferedInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileNotFoundException
import java.io.IOException
import org.apache.commons.compress.PasswordRequiredException as CommonsPasswordRequired

/** Opens any supported archive, choosing the reader from the file's content (not its name). */
object Archives {
    private val OFFSET_RE = Regex("\nOPENRAR_OFFSET=(\\d+) *\n")
    private const val LINUX_SFX_MAGIC = "#!/bin/sh\n# OpenRAR self-extracting archive"

    val ARCHIVE_EXTENSIONS = setOf(
        "zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz", "001", "cbz", "cbr", "cb7", "jar", "apk",
        "run", "exe", "zst", "tzst",
    )

    /**
     * Native RAR reader registered by the Android app (7-Zip's decoder: RAR5, encrypted headers, volumes).
     * When unset (JVM tests), RAR 1.5 - 4.x is read with junrar and RAR5 is reported as unsupported.
     */
    @Volatile
    var nativeRar: ((File, CharArray?) -> ArchiveReader)? = null

    enum class Kind { ZIP, SEVEN_ZIP, RAR4, RAR5, GZ, BZ2, XZ, ZSTD, TAR, LINUX_SFX, EXE, UNKNOWN }

    fun looksLikeArchive(name: String): Boolean {
        val low = name.lowercase()
        val ext = low.substringAfterLast('.', "")
        return ext in ARCHIVE_EXTENSIONS && ext != "exe" && ext != "run" ||
            low.endsWith(".zip.001") || low.endsWith(".7z.001")
    }

    fun sniff(head: ByteArray): Kind {
        fun starts(vararg b: Int) = head.size >= b.size && b.indices.all { (head[it].toInt() and 0xff) == b[it] }
        return when {
            starts(0x50, 0x4b, 0x03, 0x04) || starts(0x50, 0x4b, 0x05, 0x06) || starts(0x50, 0x4b, 0x07, 0x08) -> Kind.ZIP
            starts(0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c) -> Kind.SEVEN_ZIP
            starts(0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x01, 0x00) -> Kind.RAR5
            starts(0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x00) -> Kind.RAR4
            starts(0x1f, 0x8b) -> Kind.GZ
            starts(0x42, 0x5a, 0x68) -> Kind.BZ2
            starts(0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00) -> Kind.XZ
            starts(0x28, 0xb5, 0x2f, 0xfd) -> Kind.ZSTD
            head.size >= 262 && String(head, 257, 5, Charsets.ISO_8859_1) == "ustar" -> Kind.TAR
            String(head, 0, minOf(head.size, LINUX_SFX_MAGIC.length), Charsets.UTF_8) == LINUX_SFX_MAGIC -> Kind.LINUX_SFX
            starts(0x4d, 0x5a) -> Kind.EXE
            else -> Kind.UNKNOWN
        }
    }

    private fun head(f: File, n: Int): ByteArray = FileInputStream(f).use { inp ->
        val buf = ByteArray(n)
        var got = 0
        while (got < n) {
            val r = inp.read(buf, got, n - got)
            if (r < 0) break
            got += r
        }
        buf.copyOf(got)
    }

    fun open(file: File, password: CharArray? = null): ArchiveReader {
        val f = if (Volumes.isVolume(file)) Volumes.first(file) else file
        if (!f.isFile) throw FileNotFoundException(file.path)
        val volume = Volumes.isVolume(f)
        return when (sniff(head(f, 512))) {
            Kind.ZIP -> if (volume) SplitZipReader(f, password) else ZipReader(f, password)
            Kind.SEVEN_ZIP -> {
                val namesEncrypted = sevenZipNamesEncrypted(f)
                if (namesEncrypted && password == null) throw PasswordRequiredException()
                SevenZipReader(f, password, namesEncrypted)
            }
            Kind.RAR4 -> nativeRar?.invoke(f, password) ?: RarReader(f, password)
            Kind.RAR5 -> nativeRar?.invoke(f, password) ?: throw UnsupportedArchiveException(RarReader.RAR5_MESSAGE)
            Kind.GZ -> if (tarInside(f, "gz")) TarReader(f, "gz") else SingleFileReader(f, "gz")
            Kind.BZ2 -> if (tarInside(f, "bz2")) TarReader(f, "bz2") else SingleFileReader(f, "bz2")
            Kind.XZ -> if (tarInside(f, "xz")) TarReader(f, "xz") else SingleFileReader(f, "xz")
            Kind.TAR -> TarReader(f, "")
            Kind.LINUX_SFX -> {
                val m = OFFSET_RE.find(String(head(f, 65536), Charsets.UTF_8))
                    ?: throw CorruptArchiveException("Damaged self-extracting archive")
                TarReader(f, "gz", m.groupValues[1].toLong())
            }
            Kind.EXE -> try {
                ZipReader(f, password).also { if (it.entries.isEmpty()) throw IOException("no zip") }
            } catch (e: Exception) {
                if (e is PasswordRequiredException || e is WrongPasswordException) throw e
                val rar = nativeRar
                if (rar != null && containsRarSignature(f)) return rar(f, password)       // RAR self-extracting .exe
                throw UnsupportedArchiveException("${f.name} is a program, not an archive OpenRAR can open on Android.")
            }
            Kind.ZSTD -> throw UnsupportedArchiveException("Zstandard (.zst) archives are not supported on Android yet.")
            Kind.UNKNOWN -> throw UnsupportedArchiveException("${f.name} is not a recognised archive, or it is damaged.")
        }
    }

    private fun containsRarSignature(f: File): Boolean {
        val sig = byteArrayOf(0x52, 0x61, 0x72, 0x21, 0x1a, 0x07)
        val head = head(f, 4 * 1024 * 1024)
        return (0..head.size - sig.size).any { i -> sig.indices.all { head[i + it] == sig[it] } }
    }

    private fun sevenZipNamesEncrypted(f: File): Boolean = try {
        val b = SevenZFile.builder()
        if (Volumes.isVolume(f)) b.setSeekableByteChannel(SplitFileChannel.read(Volumes.list(f))) else b.setFile(f)
        b.get().close()
        false
    } catch (e: CommonsPasswordRequired) {
        true
    } catch (e: IOException) {
        throw CorruptArchiveException("Not a valid 7z archive: ${e.message}", e)
    }

    private fun tarInside(f: File, compression: String): Boolean = try {
        decompress(BufferedInputStream(FileInputStream(f)), compression).use { d ->
            val block = ByteArray(512)
            var got = 0
            while (got < 512) {
                val r = d.read(block, got, 512 - got)
                if (r < 0) break
                got += r
            }
            got == 512 && (String(block, 257, 5, Charsets.ISO_8859_1) == "ustar" || plausibleV7Tar(block))
        }
    } catch (_: Exception) {
        false
    }

    private fun plausibleV7Tar(b: ByteArray): Boolean {
        val stored = String(b, 148, 8, Charsets.ISO_8859_1).trim(' ', '\u0000').toIntOrNull(8) ?: return false
        var sum = 256
        for (i in b.indices) if (i !in 148 until 156) sum += b[i].toInt() and 0xff
        return stored == sum && sum != 256
    }
}
