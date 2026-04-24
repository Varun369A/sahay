package io.sahay.ui.screens.permissions

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.sahay.service.SahayAccessibilityService
import io.sahay.util.Permissions
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Onboarding permissions gate. Cannot be skipped.
 *
 * Auto-prompts mic and notifications on first show; deep-links to system
 * Accessibility settings for the third. Re-checks on every onResume so when
 * the user comes back from settings, the green ticks update automatically.
 *
 * Calls [onAllGranted] only when all three are confirmed granted.
 */
@Composable
fun PermissionsScreen(onAllGranted: () -> Unit) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val scope = rememberCoroutineScope()

    fun hasMic() = ContextCompat.checkSelfPermission(
        context, Manifest.permission.RECORD_AUDIO
    ) == PackageManager.PERMISSION_GRANTED

    fun hasNotif() = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        ContextCompat.checkSelfPermission(
            context, Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED

    fun hasA11y() = Permissions.isAccessibilityEnabled(
        context, SahayAccessibilityService::class.java.name
    )

    var micGranted by remember { mutableStateOf(hasMic()) }
    var notifGranted by remember { mutableStateOf(hasNotif()) }
    var a11yGranted by remember { mutableStateOf(hasA11y()) }

    val micLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> micGranted = granted }

    val notifLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> notifGranted = granted }

    // Re-check on every resume (user returns from Accessibility settings).
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                micGranted = hasMic()
                notifGranted = hasNotif()
                a11yGranted = hasA11y()
            }
        }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }

    // Auto-fire requests in sequence on first show.
    LaunchedEffect(Unit) {
        if (!micGranted) {
            micLauncher.launch(Manifest.permission.RECORD_AUDIO)
            delay(400)
        }
        if (!notifGranted && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    // Auto-advance the moment all three are green — and auto-enable
    // always-on listening so the user doesn't have to flip a switch.
    LaunchedEffect(micGranted, notifGranted, a11yGranted) {
        if (micGranted && notifGranted && a11yGranted) {
            delay(300)
            onAllGranted()
        }
    }
    // Auto-start wake word service when all perms land (first-time only; the
    // preference survives reopens so this is idempotent).
    LaunchedEffect(micGranted, notifGranted, a11yGranted) {
        if (micGranted && notifGranted && a11yGranted) {
            io.sahay.service.WakeWordService.start(context)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            "Almost ready",
            style = MaterialTheme.typography.displaySmall,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.fillMaxWidth(),
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "Sahay needs three things to work. All required.",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.fillMaxWidth(),
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(32.dp))

        PermissionRow(
            title = "Microphone",
            body = "So Sahay can hear your voice commands.",
            granted = micGranted,
            onAction = { micLauncher.launch(Manifest.permission.RECORD_AUDIO) },
        )
        Spacer(Modifier.height(12.dp))
        PermissionRow(
            title = "Notifications",
            body = "Sahay shows a quiet 'running' indicator while active.",
            granted = notifGranted,
            onAction = {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else { notifGranted = true }
            },
        )
        Spacer(Modifier.height(12.dp))
        PermissionRow(
            title = "Accessibility",
            body = "Lets Sahay tap and type in apps you allow. Payment apps are always blocked.",
            granted = a11yGranted,
            onAction = { Permissions.openAccessibilitySettings(context) },
        )

        Spacer(Modifier.height(40.dp))
        Button(
            onClick = {
                scope.launch {
                    // Manual continue button — only enabled when all granted.
                    onAllGranted()
                }
            },
            enabled = micGranted && notifGranted && a11yGranted,
            modifier = Modifier
                .fillMaxWidth()
                .height(54.dp),
        ) {
            Text(
                if (micGranted && notifGranted && a11yGranted) "All set — continue"
                else "Grant all to continue"
            )
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "Sahay will never act in payment or banking apps.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.fillMaxWidth(),
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun PermissionRow(
    title: String,
    body: String,
    granted: Boolean,
    onAction: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (granted)
                MaterialTheme.colorScheme.primaryContainer
            else
                MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = if (granted) Icons.Default.CheckCircle
                              else Icons.Default.RadioButtonUnchecked,
                contentDescription = null,
                tint = if (granted) MaterialTheme.colorScheme.primary
                       else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(28.dp),
            )
            Spacer(Modifier.size(16.dp))
            Column(modifier = Modifier.fillMaxWidth().weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium)
                Text(
                    body,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (!granted) {
                Spacer(Modifier.size(8.dp))
                Button(onClick = onAction) { Text("Grant") }
            }
        }
    }
}
