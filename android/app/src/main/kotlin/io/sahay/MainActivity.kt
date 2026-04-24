package io.sahay

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.lifecycle.lifecycleScope
import dagger.hilt.android.AndroidEntryPoint
import io.sahay.data.secrets.UserPreferences
import io.sahay.service.SahayAccessibilityService
import io.sahay.service.WakeWordService
import io.sahay.ui.SahayApp
import io.sahay.ui.theme.SahayTheme
import io.sahay.util.Permissions
import javax.inject.Inject
import kotlinx.coroutines.launch

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject lateinit var userPrefs: UserPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { Root() }
        maybeStartWakeWord()
    }

    override fun onResume() {
        super.onResume()
        // Re-check every time user returns — covers the case where accessibility
        // was granted mid-session, or wake word was toggled in Settings.
        maybeStartWakeWord()
    }

    /** Start wake-word service if user enabled it AND all permissions landed. */
    private fun maybeStartWakeWord() {
        lifecycleScope.launch {
            val snap = userPrefs.snapshot()
            val ready = Permissions.allGranted(
                this@MainActivity,
                SahayAccessibilityService::class.java.name,
            )
            if (ready && snap.wakeWordEnabled) {
                WakeWordService.start(this@MainActivity)
            }
        }
    }
}

@Composable
private fun Root() {
    SahayTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            SahayApp()
        }
    }
}
