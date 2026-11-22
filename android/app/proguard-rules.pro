# Optional codecs referenced by commons-compress / junrar that the app does not ship
-dontwarn org.brotli.dec.**
-dontwarn com.github.luben.zstd.**
-dontwarn org.objectweb.asm.**
-dontwarn org.osgi.**
-dontwarn org.slf4j.**
-dontwarn java.lang.invoke.**
-dontwarn javax.annotation.**
-dontwarn edu.umd.cs.findbugs.annotations.**
# commons-compress / commons-io look some classes up by name
-keep class org.apache.commons.compress.archivers.** { *; }
-keep class org.apache.commons.compress.compressors.** { *; }
-keep class org.tukaani.xz.** { *; }
# 7-Zip-JBinding: the native library looks its Java classes, fields and methods up by name (JNI)
-keep class net.sf.sevenzipjbinding.** { *; }
-keep interface net.sf.sevenzipjbinding.** { *; }
