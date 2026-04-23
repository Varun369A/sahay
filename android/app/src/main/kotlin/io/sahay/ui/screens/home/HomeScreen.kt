package io.sahay.ui.screens.home

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.sahay.R

@Composable
fun HomeScreen() {
    val vm: HomeViewModel = hiltViewModel()
    val state by vm.state.collectAsStateWithLifecycle()

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(48.dp))
        Text(
            "Sahay",
            style = MaterialTheme.typography.displayMedium,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(64.dp))

        val micColor = when (state.mode) {
            HomeState.Mode.Listening -> MaterialTheme.colorScheme.error
            HomeState.Mode.Thinking, HomeState.Mode.Running -> MaterialTheme.colorScheme.secondary
            HomeState.Mode.Idle -> MaterialTheme.colorScheme.primary
        }

        Box(
            modifier = Modifier
                .size(220.dp)
                .clip(CircleShape)
                .background(micColor)
                .pointerInput(Unit) {
                    detectTapGestures(
                        onPress = {
                            vm.startListening()
                            tryAwaitRelease()
                            vm.stopListening()
                        },
                    )
                },
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Default.Mic,
                contentDescription = "Mic",
                tint = Color.White,
                modifier = Modifier.size(96.dp),
            )
        }
        Spacer(Modifier.height(24.dp))
        Text(
            text = when (state.mode) {
                HomeState.Mode.Idle -> stringResource(R.string.home_tap_to_speak)
                HomeState.Mode.Listening -> stringResource(R.string.home_listening)
                HomeState.Mode.Thinking -> stringResource(R.string.home_thinking)
                HomeState.Mode.Running -> "Running…"
            },
            style = MaterialTheme.typography.titleMedium,
        )
        if (state.transcript.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            Text(
                "\"${state.transcript}\"",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        if (state.errorKey != null) {
            Spacer(Modifier.height(16.dp))
            Text(state.errorKey, color = MaterialTheme.colorScheme.error)
        }
        if (state.lastResult.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            Text(state.lastResult, color = MaterialTheme.colorScheme.primary)
        }
    }
}
