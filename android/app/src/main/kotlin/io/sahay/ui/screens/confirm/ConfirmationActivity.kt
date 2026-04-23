package io.sahay.ui.screens.confirm

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.sahay.R
import io.sahay.data.api.PlannedStepOut
import io.sahay.ui.theme.SahayTheme
import kotlinx.coroutines.CompletableDeferred
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * The trust moat. Any destructive action MUST go through this activity.
 * User explicitly taps Proceed or Cancel before the action fires.
 *
 * Usage from a coroutine:
 *     val approved = ConfirmationActivity.requestApproval(context, step)
 */
class ConfirmationActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val payload = intent.getStringExtra(EXTRA_STEP_JSON) ?: run { finish(); return }
        val step = Json.decodeFromString<PlannedStepOut>(payload)

        setContent {
            SahayTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    ConfirmContent(
                        step = step,
                        onProceed = { resolve(true) },
                        onCancel = { resolve(false) },
                    )
                }
            }
        }
    }

    private fun resolve(approved: Boolean) {
        pendingResolver?.complete(approved)
        pendingResolver = null
        finish()
    }

    companion object {
        private const val EXTRA_STEP_JSON = "step_json"

        @Volatile private var pendingResolver: CompletableDeferred<Boolean>? = null

        suspend fun requestApproval(context: Context, step: PlannedStepOut): Boolean {
            val deferred = CompletableDeferred<Boolean>()
            pendingResolver = deferred
            val intent = Intent(context, ConfirmationActivity::class.java).apply {
                putExtra(EXTRA_STEP_JSON, Json.encodeToString(PlannedStepOut.serializer(), step))
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }
            context.startActivity(intent)
            return deferred.await()
        }
    }
}

@Composable
private fun ConfirmContent(
    step: PlannedStepOut,
    onProceed: () -> Unit,
    onCancel: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            stringResource(R.string.confirm_send),
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(24.dp))
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(step.description, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                Text("Action: ${step.action}", style = MaterialTheme.typography.bodyMedium)
                Text("Screen: ${step.targetScreen}", style = MaterialTheme.typography.bodyMedium)
                val textParam = (step.params["text"] as? JsonPrimitive)?.contentOrNull
                if (textParam != null) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "\"$textParam\"",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }
        Spacer(Modifier.height(32.dp))
        Button(
            onClick = onProceed,
            modifier = Modifier.fillMaxWidth().height(54.dp),
        ) { Text(stringResource(R.string.confirm_proceed)) }
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = onCancel,
            modifier = Modifier.fillMaxWidth().height(54.dp),
        ) { Text(stringResource(R.string.confirm_cancel)) }
    }
}
