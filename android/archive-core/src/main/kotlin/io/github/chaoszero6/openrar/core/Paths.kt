package io.github.chaoszero6.openrar.core

import java.io.File

object Paths {
    private val BAD_CHARS = Regex("[\\\\:*?\"<>|\\u0000-\\u001f]")

    /** Normalise an archive member name: '/' separators, no leading '/', no '.' segments. */
    fun normalize(name: String): String =
        name.replace('\\', '/').split('/').filter { it.isNotEmpty() && it != "." }.joinToString("/")

    /**
     * Safe relative path for extraction: refuses '..' and absolute paths (zip-slip) and replaces characters
     * that SD cards (FAT/exFAT) reject. Returns null for entries that would escape the destination.
     */
    fun safeRelative(name: String): String? {
        val parts = normalize(name).split('/').filter { it.isNotEmpty() }
        if (parts.isEmpty() || parts.any { it == ".." }) return null
        return parts.joinToString("/") { p -> p.replace(BAD_CHARS, "_").trimEnd(' ', '.').ifEmpty { "_" } }
    }

    fun uniqueFile(f: File): File {
        if (!f.exists()) return f
        val base = f.name.substringBeforeLast('.', f.name)
        val ext = if (f.name.contains('.')) "." + f.name.substringAfterLast('.') else ""
        var i = 1
        while (true) {
            val c = File(f.parentFile, "$base ($i)$ext")
            if (!c.exists()) return c
            i++
        }
    }

    /** "photos.tar.gz" -> "photos", "a.7z.001" -> "a", "b.part1.rar" -> "b" */
    fun stripArchiveExtension(name: String): String {
        var n = name.replace(Regex("\\.\\d{3}$"), "").replace(Regex("(?i)\\.part\\d+\\.rar$"), "")
        for (ext in listOf(".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tgz", ".tbz2", ".txz")) {
            if (n.lowercase().endsWith(ext)) return n.dropLast(ext.length)
        }
        if (n.contains('.')) n = n.substringBeforeLast('.')
        return n
    }

    fun humanSize(n: Long): String {
        if (n < 1024) return "$n B"
        val units = listOf("KB", "MB", "GB", "TB")
        var v = n / 1024.0
        var i = 0
        while (v >= 1024 && i < units.size - 1) {
            v /= 1024
            i++
        }
        return if (v >= 100) "%.0f %s".format(v, units[i]) else "%.1f %s".format(v, units[i])
    }
}
