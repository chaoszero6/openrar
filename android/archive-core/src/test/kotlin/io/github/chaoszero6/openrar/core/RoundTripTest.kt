package io.github.chaoszero6.openrar.core

import org.apache.commons.compress.archivers.zip.ZipArchiveEntry
import org.apache.commons.compress.archivers.zip.ZipArchiveOutputStream
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.io.TempDir
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.EnumSource
import java.io.File
import kotlin.random.Random

class RoundTripTest {
    @TempDir
    lateinit var tmp: File

    private fun tree(): File {
        val d = File(tmp, "src/Holiday").apply { mkdirs() }
        File(d, "notes.txt").writeText("note ".repeat(20000))
        File(d, "Photos").mkdirs()
        File(d, "Photos/beach ü.jpg").writeBytes(Random(1).nextBytes(300_000))
        File(d, "empty").mkdirs()
        return d
    }

    private fun same(a: File, b: File) {
        a.walkTopDown().filter { it.isFile }.forEach { f ->
            val g = File(b, f.relativeTo(a).path)
            assertTrue(g.isFile, "missing ${g.path}")
            assertTrue(f.readBytes().contentEquals(g.readBytes()), g.path)
        }
        assertTrue(File(b, "empty").isDirectory)
    }

    private fun export(f: File) {
        // optional: copy Android-made archives out so the desktop app can be checked against them
        System.getProperty("openrar.export")?.takeIf { it.isNotEmpty() }?.let { f.copyTo(File(it, f.name), true) }
    }

    @ParameterizedTest
    @EnumSource(Format::class)
    fun `create and extract every format`(format: Format) {
        val src = tree()
        val dest = File(tmp, "out." + format.extension)
        val outs = ArchiveWriter.create(dest, listOf(src), CreateOptions(format, level = 5, testAfter = true))
        assertEquals(listOf(dest), outs)
        export(dest)
        Archives.open(dest).use { r ->
            assertEquals(format.label, r.format)
            val res = r.extract(null, ExtractOptions(File(tmp, "x")))
            assertEquals(emptyList<String>(), res.errors)
        }
        same(src, File(tmp, "x/Holiday"))
    }

    @Test
    fun `zip with AES-256`() {
        val src = tree()
        val z = File(tmp, "secret.zip")
        ArchiveWriter.create(z, listOf(src), CreateOptions(Format.ZIP, password = "pässword".toCharArray()))
        export(z)
        Archives.open(z).use { r -> assertTrue(r.entries.filter { !it.isDirectory }.all { it.encrypted }) }
        Archives.open(z, "pässword".toCharArray()).use { it.extract(null, ExtractOptions(File(tmp, "x"))) }
        same(src, File(tmp, "x/Holiday"))
        assertThrows<UnsupportedArchiveException> {
            ArchiveWriter.create(File(tmp, "a.7z"), listOf(src), CreateOptions(Format.SEVEN_ZIP, password = "x".toCharArray()))
        }
    }

    @ParameterizedTest
    @EnumSource(value = Format::class, names = ["ZIP", "SEVEN_ZIP", "TAR_GZ"])
    fun `split into volumes`(format: Format) {
        val src = tree()
        val dest = File(tmp, "vol." + format.extension)
        val outs = ArchiveWriter.create(dest, listOf(src), CreateOptions(format, level = 0, volumeSize = 100_000))
        assertTrue(outs.size >= 4, "volumes: $outs")
        assertEquals(dest.name + ".001", outs.first().name)
        assertTrue(outs.dropLast(1).all { it.length() == 100_000L })
        outs.forEach { export(it) }
        Archives.open(outs[1]).use { r ->                 // any volume opens the set
            assertEquals(outs.size, r.info().volumes)
            assertEquals(emptyList<String>(), r.extract(null, ExtractOptions(File(tmp, "x"))).errors)
        }
        same(src, File(tmp, "x/Holiday"))
    }

    @Test
    fun `selected entries, flat extraction and folder prefix`() {
        val z = File(tmp, "a.zip")
        ArchiveWriter.create(z, listOf(tree()), CreateOptions())
        Archives.open(z).use { r ->
            assertEquals(setOf("Holiday/Photos", "Holiday/Photos/beach ü.jpg"), r.expand(listOf("Holiday/Photos")))
            r.extract(listOf("Holiday/Photos"), ExtractOptions(File(tmp, "sel")))
            assertTrue(File(tmp, "sel/Holiday/Photos/beach ü.jpg").isFile)
            assertFalse(File(tmp, "sel/Holiday/notes.txt").exists())
            r.extract(null, ExtractOptions(File(tmp, "flat"), keepPaths = false))
            assertEquals(setOf("notes.txt", "beach ü.jpg"), File(tmp, "flat").list()!!.toSet())
            r.extract(listOf("Holiday/Photos"), ExtractOptions(File(tmp, "strip"), stripPrefix = "Holiday/Photos"))
            assertTrue(File(tmp, "strip/beach ü.jpg").isFile)
        }
    }

    @Test
    fun `overwrite modes`() {
        val z = File(tmp, "a.zip")
        ArchiveWriter.create(z, listOf(tree()), CreateOptions())
        val out = File(tmp, "o")
        Archives.open(z).use { it.extract(null, ExtractOptions(out)) }
        val notes = File(out, "Holiday/notes.txt")
        notes.writeText("mine")
        Archives.open(z).use { it.extract(null, ExtractOptions(out, OverwriteMode.SKIP)) }
        assertEquals("mine", notes.readText())
        var asked = 0
        Archives.open(z).use { r ->
            r.extract(null, ExtractOptions(out, OverwriteMode.ASK)) { _, _ -> asked++; OverwriteAnswer.NO_ALL }
        }
        assertEquals(1, asked)
        Archives.open(z).use { it.extract(null, ExtractOptions(out, OverwriteMode.RENAME)) }
        assertTrue(File(out, "Holiday/notes (1).txt").isFile)
        Archives.open(z).use { it.extract(null, ExtractOptions(out, OverwriteMode.OVERWRITE)) }
        assertEquals("note ".repeat(20000), notes.readText())
        Archives.open(z).use { r ->
            assertThrows<CancelledException> {
                r.extract(null, ExtractOptions(out, OverwriteMode.ASK)) { _, _ -> OverwriteAnswer.CANCEL }
            }
        }
    }

    @Test
    fun `zip slip is blocked`() {
        val z = File(tmp, "evil.zip")
        ZipArchiveOutputStream(z).use { o ->
            for (n in listOf("../evil.txt", "/abs.txt", "ok/fine.txt", "a/../../b.txt")) {
                o.putArchiveEntry(ZipArchiveEntry(n))
                o.write("x".toByteArray())
                o.closeArchiveEntry()
            }
        }
        val out = File(tmp, "safe")
        Archives.open(z).use { it.extract(null, ExtractOptions(out)) }
        assertFalse(File(tmp, "evil.txt").exists())
        assertFalse(File(tmp, "b.txt").exists())
        assertTrue(File(out, "ok/fine.txt").isFile)
        assertTrue(File(out, "abs.txt").isFile)          // leading '/' is dropped, stays inside
        assertNull(Paths.safeRelative("x/../../y"))
        assertEquals("a_b_.txt", Paths.safeRelative("a:b?.txt"))
    }

    @Test
    fun `edit zip archives`() {
        val z = File(tmp, "e.zip")
        ArchiveWriter.create(z, listOf(tree()), CreateOptions(password = "pw".toCharArray()))
        ZipEditor.delete(z, listOf("Holiday/notes.txt"))
        ZipEditor.rename(z, "Holiday/Photos", "Pictures")          // needs no password
        val extra = File(tmp, "extra.txt").apply { writeText("added") }
        ZipEditor.add(z, listOf(extra), "Holiday/Pictures", "pw".toCharArray())
        Archives.open(z, "pw".toCharArray()).use { r ->
            val names = r.entries.filter { !it.isDirectory }.map { it.path }.toSet()
            assertEquals(setOf("Holiday/Pictures/beach ü.jpg", "Holiday/Pictures/extra.txt"), names)
            assertTrue(r.entries.first { it.path.endsWith("extra.txt") }.encrypted)
            assertEquals(emptyList<String>(), r.test())
        }
        val t = File(tmp, "t.tar")
        ArchiveWriter.create(t, listOf(tree()), CreateOptions(Format.TAR))
        assertThrows<UnsupportedArchiveException> { ZipEditor.delete(t, listOf("Holiday")) }
    }

    @Test
    fun `convert between formats`() {
        val src = tree()
        val z = File(tmp, "c.zip")
        ArchiveWriter.create(z, listOf(src), CreateOptions(password = "pw".toCharArray()))
        val out = File(tmp, "c.7z")
        Converter.convert(z, out, CreateOptions(Format.SEVEN_ZIP), "pw".toCharArray(), tmp)
        Archives.open(out).use { it.extract(null, ExtractOptions(File(tmp, "x"))) }
        same(src, File(tmp, "x/Holiday"))
    }

    @Test
    fun `tree view derives folders`() {
        val t = ArchiveTree(listOf(
            ArchiveEntry("a/b/c.txt", false, 10),
            ArchiveEntry("a/d.txt", false, 5),
            ArchiveEntry("e.txt", false, 1),
        ))
        assertEquals(listOf("a", "e.txt"), t.list("").map { it.name })
        val a = t.list("").first()
        assertTrue(a.isDirectory)
        assertEquals(15, a.size)
        assertEquals(2, a.files)
        assertEquals(listOf("b", "d.txt"), t.list("a").map { it.name })
        assertEquals(listOf("c.txt"), t.list("a/b").map { it.name })
    }

    @Test
    fun `cancel stops work`() {
        val z = File(tmp, "big.zip")
        ArchiveWriter.create(z, listOf(tree()), CreateOptions(level = 0))
        val p = object : Progress {
            var n = 0L
            override fun advance(bytes: Long) { n += bytes }
            override val isCancelled get() = n > 1000
        }
        Archives.open(z).use { r -> assertThrows<CancelledException> { r.extract(null, ExtractOptions(File(tmp, "x")), p) } }
    }
}
