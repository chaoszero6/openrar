package io.github.chaoszero6.openrar

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.github.chaoszero6.openrar.core.Archives
import io.github.chaoszero6.openrar.core.ExtractOptions
import io.github.chaoszero6.openrar.core.PasswordRequiredException
import io.github.chaoszero6.openrar.core.WrongPasswordException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.BeforeClass
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Runs on a device / emulator: 7-Zip's native RAR decoder with the desktop project's RAR fixtures. */
@RunWith(AndroidJUnit4::class)
class NativeRarTest {
    companion object {
        private lateinit var dir: File

        @BeforeClass
        @JvmStatic
        fun setUp() {
            assertTrue("native library loads", NativeRarReader.install())
            val ctx = InstrumentationRegistry.getInstrumentation().context
            dir = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "rar-test").apply { mkdirs() }
            for (n in ctx.assets.list("")!!.filter { it.endsWith(".rar.bin") }) {
                ctx.assets.open(n).use { inp -> File(dir, n.removeSuffix(".bin")).outputStream().use { inp.copyTo(it) } }
            }
        }
    }

    @Test
    fun rar5Solid() {
        Archives.open(File(dir, "rar5-solid.rar")).use { r ->
            assertEquals("RAR", r.format)
            assertTrue(r.solid)
            assertEquals(listOf("stest1.txt", "stest2.txt"), r.entries.map { it.path })
            assertEquals(emptyList<String>(), r.test())
            val out = File(dir, "out-solid").apply { deleteRecursively() }
            val res = r.extract(null, ExtractOptions(out))
            assertEquals(emptyList<String>(), res.errors)
            for (e in r.entries) assertEquals(e.size, File(out, e.path).length())
        }
    }

    @Test
    fun rar5EncryptedNames() {
        val f = File(dir, "rar5-hpsw.rar")
        try {
            Archives.open(f).close()
            fail("expected a password prompt")
        } catch (_: PasswordRequiredException) {
        }
        try {
            Archives.open(f, "nope".toCharArray()).close()
            fail("expected wrong password")
        } catch (_: WrongPasswordException) {
        }
        Archives.open(f, "password".toCharArray()).use { r ->
            assertTrue(r.namesEncrypted)
            assertEquals(2, r.entries.count { !it.isDirectory })
            assertEquals(emptyList<String>(), r.test())
        }
    }

    @Test
    fun rar5VolumesFromAnyPart() {
        Archives.open(File(dir, "rar5-vols.part2.rar")).use { r ->
            assertEquals("rar5-vols.part1.rar", r.file.name)
            assertTrue(r.entries.isNotEmpty())
            assertEquals(emptyList<String>(), r.test())
        }
    }

    @Test
    fun rar3() {
        Archives.open(File(dir, "rar3-comment-plain.rar")).use { r ->
            assertEquals(listOf("file1.txt", "file2.txt"), r.entries.map { it.path }.sorted())
            assertEquals(emptyList<String>(), r.test())
        }
    }
}
