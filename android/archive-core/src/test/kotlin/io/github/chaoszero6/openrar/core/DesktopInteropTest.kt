package io.github.chaoszero6.openrar.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.io.TempDir
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.CsvSource
import java.io.File
import java.security.MessageDigest

/** Archives made by OpenRAR desktop (src/test/fixtures/make_fixtures.py) must open and extract identically. */
class DesktopInteropTest {
    @TempDir
    lateinit var tmp: File

    private fun res(name: String) = File(javaClass.getResource("/desktop/$name")!!.toURI())

    private val manifest: Map<String, String> by lazy {
        Regex("\"([^\"]+)\": \"([0-9a-f]{64})\"").findAll(res("manifest.json").readText())
            .associate { unescape(it.groupValues[1]) to it.groupValues[2] }
    }

    private fun unescape(s: String) = Regex("\\\\u([0-9a-fA-F]{4})").replace(s) { m ->
        m.groupValues[1].toInt(16).toChar().toString()
    }

    private fun sha(f: File) = MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { "%02x".format(it) }

    private fun checkTree(dest: File) {
        for ((rel, hash) in manifest) {
            val f = File(dest, rel)
            assertTrue(f.isFile, "missing $rel")
            assertEquals(hash, sha(f), rel)
        }
        assertTrue(File(dest, "data/empty").isDirectory, "empty folder kept")
    }

    @ParameterizedTest
    @CsvSource(
        "plain.zip,,ZIP", "aes.zip,secret,ZIP", "plain.7z,,7Z", "aes.7z,secret,7Z", "names.7z,secret,7Z",
        "data.tar,,TAR", "data.tar.gz,,TAR.GZ", "data.tar.bz2,,TAR.BZ2", "data.tar.xz,,TAR.XZ",
        "setup.run,,TAR.GZ (SFX)", "setup.exe,,ZIP (SFX)", "split.7z.002,,7Z", "split.zip.001,,ZIP",
    )
    fun `desktop archive extracts identically`(name: String, password: String?, format: String) {
        Archives.open(res(name), password?.toCharArray()).use { r ->
            assertEquals(format, r.format)
            assertTrue(r.entries.any { it.path == "data/docs/ünïcødé ñame.txt" }, "unicode name")
            assertEquals(emptyList<String>(), r.test())
            val out = File(tmp, "out")
            val res = r.extract(null, ExtractOptions(out))
            assertEquals(emptyList<String>(), res.errors)
            checkTree(out)
        }
    }

    @Test
    fun `passwords and encrypted names`() {
        assertThrows<PasswordRequiredException> { Archives.open(res("names.7z")) }
        assertThrows<WrongPasswordException> { Archives.open(res("names.7z"), "nope".toCharArray()) }
        Archives.open(res("names.7z"), "secret".toCharArray()).use { assertTrue(it.info().namesEncrypted) }
        Archives.open(res("aes.zip")).use { r ->
            assertTrue(r.entries.first { !it.isDirectory }.encrypted)
            assertThrows<PasswordRequiredException> { r.extract(null, ExtractOptions(File(tmp, "a"))) }
        }
        Archives.open(res("aes.zip"), "nope".toCharArray()).use { r ->
            assertThrows<WrongPasswordException> { r.extract(null, ExtractOptions(File(tmp, "b"))) }
        }
        Archives.open(res("aes.7z"), "nope".toCharArray()).use { r ->
            assertThrows<WrongPasswordException> { r.extract(null, ExtractOptions(File(tmp, "c"))) }
        }
    }

    @Test
    fun `comment, lock flag and single files`() {
        Archives.open(res("plain.zip")).use { assertEquals("Hello from the desktop", it.comment) }
        Archives.open(res("locked.zip")).use { r ->
            assertTrue(r.locked)
            assertEquals("", r.comment)                 // the marker is hidden
        }
        val copy = res("locked.zip").copyTo(File(tmp, "l.zip"))
        assertThrows<LockedArchiveException> { ZipEditor.delete(copy, listOf("data/docs")) }
        Archives.open(res("plain.txt.gz")).use { r ->
            assertEquals("GZ", r.format)
            assertEquals(listOf("plain.txt"), r.entries.map { it.path })
            r.extract(null, ExtractOptions(tmp))
            assertEquals("single file\n".repeat(100), File(tmp, "plain.txt").readText())
        }
        Archives.open(res("single.txt.gz")).use { assertEquals("TAR.GZ", it.format) }
    }

    @Test
    fun `rar 4 opens and rar 5 explains`() {
        val data = File(System.getProperty("openrar.testdata"))
        Archives.open(File(data, "rar3-comment-plain.rar")).use { r ->
            assertEquals("RAR", r.format)
            assertTrue(r.entries.isNotEmpty())
            assertEquals(emptyList<String>(), r.test())
        }
        val e = assertThrows<UnsupportedArchiveException> { Archives.open(File(data, "rar5-solid.rar")) }
        assertTrue(e.message!!.contains("RAR5"))
    }
}
