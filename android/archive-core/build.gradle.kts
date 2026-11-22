// Archive engine in plain Kotlin/JVM, so it is unit-tested on the build machine (./gradlew :archive-core:test)
plugins {
    id("org.jetbrains.kotlin.jvm")
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    api("net.lingala.zip4j:zip4j:2.11.5")                 // ZIP incl. AES-256 / ZipCrypto
    api("org.apache.commons:commons-compress:1.26.2")     // 7Z (incl. AES), TAR, GZ, BZ2
    api("org.tukaani:xz:1.9")                             // XZ and LZMA/LZMA2 for 7Z
    api("com.github.junrar:junrar:7.5.5")                 // RAR 1.5 - 4.x (extract only)
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
    environment("LC_ALL", "C.UTF-8")              // UTF-8 file names, as on Android (CI images often default to "C")
    systemProperty("openrar.export", System.getProperty("openrar.export") ?: "")
    systemProperty("openrar.testdata", rootDir.resolve("../shared/tests/data").absolutePath)
}
