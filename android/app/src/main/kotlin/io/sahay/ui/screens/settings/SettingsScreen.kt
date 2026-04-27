package io.sahay.ui.screens.settings

import android.app.TimePickerDialog
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Slider
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.sahay.R
import io.sahay.engine.QuietHours

@Composable
fun SettingsScreen(onLoggedOut: () -> Unit) {
    val vm: SettingsViewModel = hiltViewModel()
    val state by vm.state.collectAsStateWithLifecycle()
    var showDeleteDialog by remember { mutableStateOf(false) }

    // Re-check notification-listener grant when the user returns from system settings.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) vm.refreshPermissionGrants()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    if (state.deleted) {
        onLoggedOut()
        return
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            stringResource(R.string.settings_title),
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.primary,
        )

        // ─── Assistant identity + always-listen (unified) ──────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("Your assistant", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(4.dp))
                Text(
                    "Pick a name. Say the name to activate — that's your wake word.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = state.assistantName,
                    onValueChange = vm::setAssistantName,
                    label = { Text("Name") },
                    placeholder = { Text("e.g. Sahay, Pluto, Jarvis") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "Common English words work best with offline recognition. " +
                        "Examples: Sahay, Pluto, Jarvis, Alfred, Echo, Nova.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(16.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("Always listening", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "Hold Sahay ready 24/7. Say \"${state.assistantName}, …\" anywhere.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = state.wakeWordEnabled,
                        onCheckedChange = vm::setWakeWordEnabled,
                    )
                }
            }
        }

        // ─── Privacy: screenshot upload (off by default) ────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("Privacy", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            "Send screenshots to assist",
                            style = MaterialTheme.typography.titleSmall,
                        )
                        Text(
                            "Off by default. When off, Sahay works only from " +
                                "the on-screen text and buttons it can read — your " +
                                "screen pixels never leave the device. Turn on only " +
                                "if Sahay is missing visual context for a task.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = state.screenshotUploadEnabled,
                        onCheckedChange = vm::setScreenshotUploadEnabled,
                    )
                }
            }
        }

        // ─── Role picker (tailors tone + doc templates) ─────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(
                    stringResource(R.string.settings_role_title),
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    stringResource(R.string.settings_role_body),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    SAHAY_ROLES.forEach { (key, label) ->
                        FilterChip(
                            selected = state.role == key,
                            onClick = { vm.setRole(key) },
                            label = { Text(label) },
                        )
                    }
                }
            }
        }

        // ─── Mood picker (personality / tone) ───────────────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(
                    stringResource(R.string.settings_mood_title),
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    stringResource(R.string.settings_mood_body),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    SAHAY_MOODS.forEach { (key, label, _) ->
                        FilterChip(
                            selected = (state.mood ?: "default") == key,
                            onClick = { vm.setMood(key) },
                            label = { Text(label) },
                        )
                    }
                }
                val selectedDesc = SAHAY_MOODS.firstOrNull {
                    it.first == (state.mood ?: "default")
                }?.third
                if (!selectedDesc.isNullOrBlank()) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        selectedDesc,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        // ─── Notification awareness ─────────────────────────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            stringResource(R.string.settings_notifications),
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Text(
                            stringResource(R.string.settings_notifications_body),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = state.notificationsEnabled &&
                            state.notificationListenerGranted,
                        onCheckedChange = vm::setNotificationsEnabled,
                        enabled = state.notificationListenerGranted,
                    )
                }
                if (!state.notificationListenerGranted) {
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(
                        onClick = vm::openNotificationListenerSettings,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(stringResource(R.string.settings_notifications_grant))
                    }
                }
            }
        }

        // ─── Proactive announcements + Quiet Hours ──────────────────
        if (state.notificationListenerGranted && state.notificationsEnabled) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                stringResource(R.string.settings_proactive_title),
                                style = MaterialTheme.typography.titleMedium,
                            )
                            Text(
                                stringResource(R.string.settings_proactive_body),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(
                            checked = state.proactiveAnnounceEnabled,
                            onCheckedChange = vm::setProactiveAnnounceEnabled,
                        )
                    }

                    if (state.proactiveAnnounceEnabled) {
                        Spacer(Modifier.height(16.dp))
                        HorizontalDivider()
                        Spacer(Modifier.height(12.dp))
                        Text(
                            stringResource(R.string.settings_quiet_hours_title),
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Text(
                            stringResource(R.string.settings_quiet_hours_body),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(12.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            TimePickerButton(
                                label = stringResource(R.string.settings_quiet_hours_start),
                                totalMinutes = state.quietHoursStartMin,
                                onPicked = vm::setQuietHoursStart,
                                modifier = Modifier.weight(1f),
                            )
                            TimePickerButton(
                                label = stringResource(R.string.settings_quiet_hours_end),
                                totalMinutes = state.quietHoursEndMin,
                                onPicked = vm::setQuietHoursEnd,
                                modifier = Modifier.weight(1f),
                            )
                        }

                        Spacer(Modifier.height(16.dp))
                        Text(
                            stringResource(R.string.settings_dnd_threshold),
                            style = MaterialTheme.typography.titleSmall,
                        )
                        Text(
                            stringResource(
                                R.string.settings_dnd_threshold_body,
                                state.dndImportanceThreshold,
                            ),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Slider(
                            value = state.dndImportanceThreshold.toFloat(),
                            onValueChange = { vm.setDndImportanceThreshold(it.toInt()) },
                            valueRange = 5f..10f,
                            steps = 4,
                            modifier = Modifier.fillMaxWidth(),
                        )

                        Spacer(Modifier.height(8.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    stringResource(R.string.settings_quiet_whisper),
                                    style = MaterialTheme.typography.titleSmall,
                                )
                                Text(
                                    stringResource(R.string.settings_quiet_whisper_body),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Switch(
                                checked = state.quietHoursWhisper,
                                onCheckedChange = vm::setQuietHoursWhisper,
                            )
                        }

                        Spacer(Modifier.height(12.dp))
                        HorizontalDivider()
                        Spacer(Modifier.height(12.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    stringResource(R.string.settings_morning_briefing_title),
                                    style = MaterialTheme.typography.titleMedium,
                                )
                                Text(
                                    stringResource(R.string.settings_morning_briefing_body),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Switch(
                                checked = state.morningBriefingEnabled,
                                onCheckedChange = vm::setMorningBriefingEnabled,
                            )
                        }
                    }
                }
            }
        }

        // ─── Account ────────────────────────────────────────────────
        state.me?.let { me ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Account", style = MaterialTheme.typography.titleMedium)
                    Text(me.email, style = MaterialTheme.typography.bodyMedium)
                    Text("Tier: ${me.tier} · ${me.locale}", style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        // ─── Usage ──────────────────────────────────────────────────
        state.usage?.let { u ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Usage today", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(8.dp))
                    Text("${u.dailyCount} / ${u.dailyLimit} actions", style = MaterialTheme.typography.bodyMedium)
                    LinearProgressIndicator(
                        progress = { (u.dailyCount.toFloat() / u.dailyLimit.coerceAtLeast(1)).coerceIn(0f, 1f) },
                        modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                    )
                    Spacer(Modifier.height(8.dp))
                    Text("This week: ${u.weeklyCount} / ${u.weeklyLimit}", style = MaterialTheme.typography.bodySmall)
                    Text("Image generations this month: ${u.monthlyImageGensUsed} / ${u.monthlyImageGensLimit}",
                         style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        // ─── App allowlist ──────────────────────────────────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(stringResource(R.string.settings_apps), style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(4.dp))
                Text(
                    stringResource(R.string.settings_payment_blocked),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
                Spacer(Modifier.height(8.dp))
                if (state.grants.isEmpty()) {
                    Text("No apps allowed yet. Allowed apps will appear here.",
                         style = MaterialTheme.typography.bodyMedium,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    state.grants.forEach { g ->
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            Text(g.appPackage, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                            TextButton(onClick = { vm.revoke(g.appPackage) }) { Text("Revoke") }
                        }
                    }
                }
            }
        }

        // ─── Kill switch hint ───────────────────────────────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(stringResource(R.string.settings_kill_switch_hint),
                     style = MaterialTheme.typography.bodyMedium)
            }
        }

        // ─── Privacy ────────────────────────────────────────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(stringResource(R.string.settings_privacy), style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                Text(
                    "Sahay processes screen content on-device. Only abstract action metadata reaches the server.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedButton(
                    onClick = { showDeleteDialog = true },
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                ) {
                    Text(stringResource(R.string.settings_delete_data))
                }
            }
        }

        // ─── Logout ─────────────────────────────────────────────────
        Button(onClick = onLoggedOut, modifier = Modifier.fillMaxWidth().height(48.dp)) {
            Text("Log out")
        }

        state.errorKey?.let { err ->
            Text(err, color = MaterialTheme.colorScheme.error)
        }
    }

    if (showDeleteDialog) {
        AlertDialog(
            onDismissRequest = { showDeleteDialog = false },
            title = { Text("Delete account & data?") },
            text = { Text("This permanently deletes your account, files, and all action history. Cannot be undone.") },
            confirmButton = {
                Button(
                    onClick = { showDeleteDialog = false; vm.deleteMyData() },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                ) { Text("Delete forever") }
            },
            dismissButton = {
                TextButton(onClick = { showDeleteDialog = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun TimePickerButton(
    label: String,
    totalMinutes: Int,
    onPicked: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    OutlinedButton(
        onClick = {
            val h = (totalMinutes / 60).coerceIn(0, 23)
            val m = (totalMinutes % 60).coerceIn(0, 59)
            TimePickerDialog(
                context,
                { _, newH, newM -> onPicked(newH * 60 + newM) },
                h, m, true,
            ).show()
        },
        modifier = modifier,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(label, style = MaterialTheme.typography.labelSmall)
            Text(
                QuietHours.formatHm(totalMinutes),
                style = MaterialTheme.typography.titleMedium,
            )
        }
    }
}
