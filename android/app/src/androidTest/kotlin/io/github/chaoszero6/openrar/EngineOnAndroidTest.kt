package io.github.chaoszero6.openrar

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.github.chaoszero6.openrar.core.ArchiveWriter
import io.github.chaoszero6.openrar.core.Archives
import io.github.chaoszero6.openrar.core.CreateOptions
import io.github.chaoszero6.openrar.core.ExtractOptions
import io.github.chaoszero6.openrar.core.Format
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.MessageDigest
import kotlin.random.Random

/** The archive engine on Android's runtime: desktop-made fixtures and every writable format. */
@RunWith(AndroidJUnit4::class)
class EngineOnAndroidTest {
    companion object {
        private lateinit var dir: File
        private lateinit var manifest: Map<String, String>

        @BeforeClass
        @JvmStatic
        fun setUp() {
            NativeRarReader.install()
            val ctx = InstrumentationRegistry.getInstrumentation().context
            dir = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "engine-test").apply {
                deleteRecursively()
                mkdirs()
            }
            for (n in ctx.assets.list("")!!.filter { it.endsWith(".bin") }) {
                ctx.assets.open(n).use { inp -> File(dir, n.removeSuffix(".bin")).outputStream().use { inp.copyTo(it) } }
            }
            manifest = Regex("\"([^\"]+)\": \"([0-9a-f]{64})\"").findAll(File(dir, "manifest.json").readText())
                .associate { m ->
                    Regex("\\\\u([0-9a-fA-F]{4})").replace(m.groupValues[1]) { it.groupValues[1].toInt(16).toChar().toString() } to
                        m.groupValues[2]
                }
        }

        private fun sha(f: File) = MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { "%02x".format(it) }
    }

    @Test
    fun desktopArchives() {
        val cases = listOf("plain.zip" to null, "aes.zip" to "secret", "plain.7z" to null, "aes.7z" to "secret",
            "names.7z" to "secret", "data.tar" to null, "data.tar.gz" to null, "data.tar.bz2" to null,
            "data.tar.xz" to null, "setup.run" to null, "setup.exe" to null, "split.7z.001" to null, "split.zip.001" to null)
        for ((name, pw) in cases) {
            val out = File(dir, "out-$name")
            Archives.open(File(dir, name), pw?.toCharArray()).use { r ->
                assertEquals(name, emptyList<String>(), r.test())
                assertEquals(name, emptyList<String>(), r.extract(null, ExtractOptions(out)).errors)
            }
            for ((rel, hash) in manifest) assertEquals("$name: $rel", hash, sha(File(out, rel)))
            assertTrue(name, File(out, "data/empty").isDirectory)
        }
    }

    @Test
    fun createEveryFormat() {
        val src = File(dir, "src/Holiday").apply { mkdirs() }
        File(src, "notes ü.txt").writeText("note ".repeat(20000))
        File(src, "photo.jpg").writeBytes(Random(3).nextBytes(200_000))
        for (fmt in Format.entries) {
            val pw = if (fmt.canEncrypt) "pw".toCharArray() else null
            val dest = File(dir, "made.${fmt.extension}")
            ArchiveWriter.create(dest, listOf(src), CreateOptions(fmt, 5, pw, testAfter = true))
            val out = File(dir, "x-${fmt.name}")
            Archives.open(dest, pw).use { assertEquals(emptyList<String>(), it.extract(null, ExtractOptions(out)).errors) }
            assertEquals(fmt.label, sha(File(src, "photo.jpg")), sha(File(out, "Holiday/photo.jpg")))
            assertEquals(fmt.label, sha(File(src, "notes ü.txt")), sha(File(out, "Holiday/notes ü.txt")))
        }
        val vols = ArchiveWriter.create(File(dir, "v.7z"), listOf(src), CreateOptions(Format.SEVEN_ZIP, 0, volumeSize = 64 * 1024))
        assertTrue(vols.size > 2)
        Archives.open(vols.last()).use { assertEquals(emptyList<String>(), it.test()) }
    }
}
