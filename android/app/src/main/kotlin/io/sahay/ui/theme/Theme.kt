package io.sahay.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val LightColors = lightColorScheme(
    primary = Indigo,
    onPrimary = Cream,
    primaryContainer = IndigoLight,
    onPrimaryContainer = IndigoDark,
    secondary = Saffron,
    onSecondary = Charcoal,
    background = Cream,
    onBackground = Charcoal,
    surface = Cream,
    onSurface = Charcoal,
    surfaceVariant = Mist,
    onSurfaceVariant = Slate,
    error = Danger,
    onError = Cream,
)

private val DarkColors = darkColorScheme(
    primary = IndigoLight,
    onPrimary = Charcoal,
    primaryContainer = IndigoDark,
    onPrimaryContainer = Cream,
    secondary = Saffron,
    onSecondary = Charcoal,
    background = Charcoal,
    onBackground = Cream,
    surface = Charcoal,
    onSurface = Cream,
    surfaceVariant = Slate,
    onSurfaceVariant = Mist,
    error = Danger,
    onError = Cream,
)

@Composable
fun SahayTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colors = if (darkTheme) DarkColors else LightColors
    MaterialTheme(
        colorScheme = colors,
        typography = SahayTypography,
        content = content,
    )
}
