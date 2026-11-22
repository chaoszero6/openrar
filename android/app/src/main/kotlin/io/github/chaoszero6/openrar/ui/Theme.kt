package io.github.chaoszero6.openrar.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val Light = lightColorScheme(
    primary = Color(0xFF4F46E5), onPrimary = Color.White,
    primaryContainer = Color(0xFFE0E7FF), onPrimaryContainer = Color(0xFF1E1B4B),
    secondary = Color(0xFF0EA5E9), onSecondary = Color.White,
    secondaryContainer = Color(0xFFE0F2FE), onSecondaryContainer = Color(0xFF0C4A6E),
    background = Color(0xFFF8F9FF), surface = Color(0xFFF8F9FF),
    surfaceVariant = Color(0xFFE4E5F1), error = Color(0xFFDC2626),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFA5B4FC), onPrimary = Color(0xFF1E1B4B),
    primaryContainer = Color(0xFF3730A3), onPrimaryContainer = Color(0xFFE0E7FF),
    secondary = Color(0xFF7DD3FC), onSecondary = Color(0xFF0C4A6E),
    secondaryContainer = Color(0xFF075985), onSecondaryContainer = Color(0xFFE0F2FE),
    background = Color(0xFF12131A), surface = Color(0xFF12131A),
    surfaceVariant = Color(0xFF2A2C38), error = Color(0xFFF87171),
)

/** Material You colours on Android 12+, OpenRAR indigo / sky otherwise; follows the system dark mode. */
@Composable
fun OpenRarTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val ctx = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        dark -> Dark
        else -> Light
    }
    MaterialTheme(colorScheme = scheme, content = content)
}
