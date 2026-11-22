package io.github.chaoszero6.openrar.core

import java.io.File

/** One file or folder inside an archive. Paths always use '/' and never start with '/'. */
data class ArchiveEntry(
    val path: String,
    val isDirectory: Boolean,
    val size: Long,
    val packedSize: Long? = null,
    val modified: Long? = null,          // epoch millis
    val crc: Long? = null,
    val encrypted: Boolean = false,
    val unixMode: Int = 0,
) {
    val name: String get() = path.substringAfterLast('/')
    val parent: String get() = path.substringBeforeLast('/', "")
}

data class ArchiveInfo(
    val format: String,
    val files: Int,
    val folders: Int,
    val unpackedSize: Long,
    val packedSize: Long,
    val encrypted: Boolean,
    val namesEncrypted: Boolean,
    val solid: Boolean,
    val volumes: Int,
    val comment: String,
    val locked: Boolean,
    val selfExtracting: Boolean,
)

open class ArchiveException(message: String, cause: Throwable? = null) : Exception(message, cause)
class PasswordRequiredException(message: String = "This archive is encrypted. Enter the password.") : ArchiveException(message)
class WrongPasswordException(message: String = "Wrong password", cause: Throwable? = null) : ArchiveException(message, cause)
class UnsupportedArchiveException(message: String) : ArchiveException(message)
class CorruptArchiveException(message: String, cause: Throwable? = null) : ArchiveException(message, cause)
class CancelledException : ArchiveException("Cancelled")
class LockedArchiveException : ArchiveException("This archive is locked and cannot be changed.")

enum class OverwriteMode { ASK, OVERWRITE, SKIP, RENAME }
enum class OverwriteAnswer { YES, YES_ALL, NO, NO_ALL, RENAME, CANCEL }

/** Progress and cancellation. Called from the worker thread. */
interface Progress {
    fun total(bytes: Long) {}
    fun file(name: String) {}
    fun advance(bytes: Long) {}
    val isCancelled: Boolean get() = false

    fun check() {
        if (isCancelled) throw CancelledException()
    }

    companion object {
        val NONE = object : Progress {}
    }
}

enum class Format(val label: String, val extension: String, val canEncrypt: Boolean) {
    ZIP("ZIP", "zip", true),
    SEVEN_ZIP("7Z", "7z", false),
    TAR("TAR", "tar", false),
    TAR_GZ("TAR.GZ", "tar.gz", false),
    TAR_BZ2("TAR.BZ2", "tar.bz2", false),
    TAR_XZ("TAR.XZ", "tar.xz", false);

    companion object {
        fun fromName(name: String): Format {
            val n = name.lowercase()
            return when {
                n.endsWith(".7z") -> SEVEN_ZIP
                n.endsWith(".tar.gz") || n.endsWith(".tgz") -> TAR_GZ
                n.endsWith(".tar.bz2") || n.endsWith(".tbz2") -> TAR_BZ2
                n.endsWith(".tar.xz") || n.endsWith(".txz") -> TAR_XZ
                n.endsWith(".tar") -> TAR
                else -> ZIP
            }
        }
    }
}

val LEVEL_NAMES = listOf("Store", "Fastest", "Fast", "Normal", "Good", "Best")

data class CreateOptions(
    val format: Format = Format.ZIP,
    val level: Int = 3,                  // 0 store .. 5 best
    val password: CharArray? = null,     // ZIP: AES-256
    val volumeSize: Long = 0,            // >0: split into name.ext.001, .002, ...
    val deleteSources: Boolean = false,
    val testAfter: Boolean = false,
)

data class ExtractOptions(
    val destination: File,
    val overwrite: OverwriteMode = OverwriteMode.ASK,
    val keepPaths: Boolean = true,
    /** Strip this folder prefix from entry paths (extracting a sub-folder of the archive). */
    val stripPrefix: String = "",
)

data class ExtractResult(val extracted: List<File>, val skipped: List<String>, val errors: List<String>)
