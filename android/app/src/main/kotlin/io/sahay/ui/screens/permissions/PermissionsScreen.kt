package io.sahay.ui.screens.permissions

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.sahay.R
import io.sahay.service.SahayAccessibilityService
import io.sahay.util.Permissions

@Composable
fun PermissionsScreen(onDone: () -> Unit) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle

    var hasMic by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED
        )
    }
    var hasNotif by remember {
        mutableStateOf(
            Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
                    == PackageManager.PERMISSION_GRANTED
        )
    }
    var hasA11y by remember {
        mutableStateOf(
            Permissions.isAccessibilityEnabled(context, SahayAccessibilityService::class.java.name)
        )
    }

    val micLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> hasMic = granted }

    val notifLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> hasNotif = granted }

    // Re-check accessibility when user returns from Settings.
    LaunchedEffect(Unit) {
        val obs = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                hasA11y = Permissions.isAccessibilityEnabled(
                    context, SahayAccessibilityService::class.java.name
                )
            }
        }
        lifecycle.addObserver(obs)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            "Almost there",
            style = MaterialTheme.typography.displayMedium,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(24.dp))

        PermissionCard(
            title = stringResource(R.string.perm_mic_title),
            body = stringResource(R.string.perm_mic_body),
            granted = hasMic,
            onAction = { micLauncher.launch(Manifest.permission.RECORD_AUDIO) },
        )
        Spacer(Modifier.height(12.dp))

        PermissionCard(
            title = stringResource(R.string.perm_notifications_title),
            body = stringResource(R.string.perm_notifications_body),
            granted = hasNotif,
            onAction = {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else { hasNotif = true }
            },
        )
        Spacer(Modifier.height(12.dp))

        PermissionCard(
            title = stringResource(R.string.perm_accessibility_title),
            body = stringResource(R.string.perm_accessibility_body),
            granted = hasA11y,
            onAction = { Permissions.openAccessibilitySettings(context) },
        )

        Spacer(Modifier.height(36.dp))
        Button(
            onClick = onDone,
            enabled = hasMic && hasNotif && hasA11y,
            modifier = Modifier.fillMaxWidth().height(54.dp),
        ) {
            Text("Continue")
        }
        Spacer(Modifier.height(8.dp))
        TextButton(onClick = onDone) { Text("Continue (limited mode)") }
    }
}

@Composable
private fun PermissionCard(
    title: String,
    body: String,
    granted: Boolean,
    onAction: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                title = title,
                granted = granted,
            )
            Spacer(Modifier.height(4.dp))
            Text(body, style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(12.dp))
            if (!granted) {
                Button(onClick = onAction) { Text(stringResource(R.string.perm_open_settings)) }
            }
        }
    }
}

@Composable
private fun Row(title: String, granted: Boolean) {
    androidx.compose.foundation.layout.Row(
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(0.dp))
        if (granted) {
            Icon(
                Icons.Default.Check,
                contentDescription = "granted",
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(start = 8.dp),
            )
        }
    }
}
