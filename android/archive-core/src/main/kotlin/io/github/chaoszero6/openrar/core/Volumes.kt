package io.github.chaoszero6.openrar.core

import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.channels.NonWritableChannelException
import java.nio.channels.SeekableByteChannel

/** name.ext.001, .002, ... (7-Zip / OpenRAR desktop split volumes). */
object Volumes {
    private val NUMBERED = Regex("^(.*)\\.(\\d{3})$")

    fun isVolume(f: File): Boolean = NUMBERED.matches(f.name)

    fun list(any: File): List<File> {
        val m = NUMBERED.matchEntire(any.name) ?: return listOf(any)
        val base = m.groupValues[1]
        val out = mutableListOf<File>()
        var i = 1
        while (true) {
            val f = File(any.parentFile, "%s.%03d".format(base, i))
            if (!f.exists()) break
            out.add(f)
            i++
        }
        return out.ifEmpty { listOf(any) }
    }

    fun first(any: File): File = list(any).first()
}

/**
 * A read/write seekable channel over fixed-size volume files: lets 7z (which seeks back to write its start
 * header) and ZIP/TAR write split archives directly, without a temporary full-size copy. Also reads volumes.
 */
class SplitFileChannel private constructor(
    private val base: File,
    private val volumeSize: Long,
    private val writable: Boolean,
    existing: List<File>,
) : SeekableByteChannel {
    private val files = existing.toMutableList()
    private val sizes = existing.map { it.length() }.toMutableList()
    private val handles = HashMap<Int, RandomAccessFile>()
    private var pos = 0L
    private var open = true

    val paths: List<File> get() = files.toList()

    companion object {
        fun create(base: File, volumeSize: Long): SplitFileChannel {
            require(volumeSize >= 1024) { "Volume size must be at least 1 KB" }
            return SplitFileChannel(base, volumeSize, true, emptyList())
        }

        fun read(volumes: List<File>): SplitFileChannel =
            SplitFileChannel(volumes.first(), 0, false, volumes)
    }

    private fun volumeFile(i: Int) = File(base.parentFile, base.name + ".%03d".format(i + 1))

    private fun start(i: Int): Long = if (writable) i * volumeSize else sizes.take(i).sum()

    private fun locate(p: Long): Int {
        if (writable) return (p / volumeSize).toInt()
        var s = 0L
        for (i in sizes.indices) {
            if (p < s + sizes[i]) return i
            s += sizes[i]
        }
        return sizes.size
    }

    private fun handle(i: Int): RandomAccessFile = handles.getOrPut(i) {
        while (writable && files.size <= i) {
            files.add(volumeFile(files.size))
            sizes.add(0)
        }
        RandomAccessFile(files[i], if (writable) "rw" else "r")
    }

    override fun read(dst: ByteBuffer): Int {
        if (pos >= size()) return -1
        val i = locate(pos)
        if (i >= files.size) return -1
        val h = handle(i)
        h.seek(pos - start(i))
        val n = h.channel.read(dst)
        if (n > 0) pos += n
        return n
    }

    override fun write(src: ByteBuffer): Int {
        if (!writable) throw NonWritableChannelException()
        var written = 0
        while (src.hasRemaining()) {
            val i = locate(pos)
            val h = handle(i)
            val off = pos - start(i)
            val room = (volumeSize - off).toInt()
            val chunk = src.duplicate()
            chunk.limit(chunk.position() + minOf(room, src.remaining()))
            h.seek(off)
            val n = h.channel.write(chunk)
            src.position(src.position() + n)
            pos += n
            written += n
            sizes[i] = maxOf(sizes[i], off + n)
        }
        return written
    }

    override fun position(): Long = pos

    override fun position(newPosition: Long): SeekableByteChannel {
        pos = newPosition
        return this
    }

    override fun size(): Long = if (writable) (if (files.isEmpty()) 0 else start(files.size - 1) + sizes.last()) else sizes.sum()

    override fun truncate(size: Long): SeekableByteChannel = throw IOException("truncate not supported")

    override fun isOpen(): Boolean = open

    override fun close() {
        if (!open) return
        open = false
        handles.values.forEach { it.close() }
        handles.clear()
        if (writable && files.isEmpty()) {           // empty archive: still produce .001
            volumeFile(0).createNewFile()
            files.add(volumeFile(0))
        }
    }
}
