# OpenRAR for Android

A native Kotlin app (Jetpack Compose, Material 3, Android 8.0+).

* **Browse** internal storage, SD cards and USB drives; select files and folders; rename, delete, new folder, share.
* **Open** ZIP (incl. AES and ZipCrypto), 7Z (incl. AES and encrypted file names), **RAR 1.5 – 5** (solid,
  multi-volume `.partN.rar`, encrypted names, RAR self-extracting `.exe`), TAR, TAR.GZ / BZ2 / XZ, single `.gz` /
  `.bz2` / `.xz` files, split `name.ext.001` volumes and OpenRAR's Windows `.exe` and Linux `.run` SFX archives.
* **Extract** everything or a selection, here / into a folder / anywhere, with ask / replace / skip / keep both;
  view or share single files straight from an archive; test archives; archive information and comments.
* **Create** ZIP (optional AES-256 password), 7Z, TAR, TAR.GZ, TAR.BZ2 and TAR.XZ with six compression levels,
  split volumes (10 MB … 4 GB presets), test afterwards and delete the originals.
* **Edit ZIP archives**: delete, rename, add files (respects OpenRAR's lock flag).
* **Other apps**: *Open with OpenRAR* for archives (also from browsers, mail and messaging apps) and *Compress with
  OpenRAR* in the share sheet.
* Material You colours on Android 12+, dark mode, edge-to-edge.

OpenRAR asks for *All files access* to browse and write anywhere in storage, as file managers do. Archives opened
from other apps work without it.

Not on Android (yet): creating encrypted 7Z, `.tar.zst` / Zstandard, recovery records and repair, self-extracting
archives, editing 7Z/TAR archives, ISO/CAB/ARJ.

## Layout
* `archive-core/`: the archive engine as a plain Kotlin/JVM library (zip4j, Apache Commons Compress, XZ for
  Java, junrar), so it is unit-tested on any computer. `src/test/fixtures/make_fixtures.py` regenerates the test
  archives with the desktop engine, so the tests prove Android reads exactly what the desktop app writes.
* `app/`: the Compose app; `NativeRarReader.kt` plugs 7-Zip's native RAR / RAR5 decoder (7-Zip-JBinding-4Android)
  into the engine.

## Build
Needs JDK 17+ and the Android SDK (API 34); set `sdk.dir` in `local.properties` or `ANDROID_HOME`.
```
./gradlew :archive-core:test          engine tests on the JVM (desktop interoperability, every format, volumes …)
./gradlew assembleDebug               app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease             app/build/outputs/apk/release/app-release.apk (minified)
./gradlew connectedDebugAndroidTest   on-device tests: engine on Android's runtime + native RAR5
```
For your own release signing create `keystore.properties` (kept out of git) next to this file:
```
storeFile=/path/to/release.jks
storePassword=…
keyAlias=…
keyPassword=…
```
Without it, release builds are signed with the debug key. An APK signed with a different key can't be installed
over an existing one, so keep using the same keystore for updates.
