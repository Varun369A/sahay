package io.sahay.ui.screens.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.sahay.R

@Composable
fun AuthScreen(viewModel: AuthViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    when (state.step) {
        AuthState.Step.EmailEntry -> EmailStep(state, viewModel)
        AuthState.Step.OtpEntry -> OtpStep(state, viewModel)
    }
}

@Composable
private fun EmailStep(state: AuthState, vm: AuthViewModel) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = stringResource(R.string.onboarding_title),
            style = MaterialTheme.typography.displayMedium,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = stringResource(R.string.onboarding_subtitle),
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(48.dp))
        OutlinedTextField(
            value = state.email,
            onValueChange = vm::onEmailChange,
            label = { Text(stringResource(R.string.onboarding_email_hint)) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            isError = state.errorKey == "invalid_email",
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = { vm.requestOtp(asLogin = false) },
            enabled = !state.isLoading,
            modifier = Modifier.fillMaxWidth().height(54.dp),
        ) {
            if (state.isLoading) CircularProgressIndicator(strokeWidth = 2.dp)
            else Text(stringResource(R.string.onboarding_send_otp))
        }
        Spacer(Modifier.height(8.dp))
        TextButton(onClick = { vm.requestOtp(asLogin = true) }) {
            Text("Already have an account? Log in")
        }
        if (state.errorKey != null) {
            Spacer(Modifier.height(16.dp))
            Text(
                state.errorKey,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Spacer(Modifier.height(48.dp))
        Text(
            stringResource(R.string.onboarding_privacy_promise),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun OtpStep(state: AuthState, vm: AuthViewModel) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Check your email",
            style = MaterialTheme.typography.displayMedium,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Code sent to ${state.email}",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(40.dp))
        OutlinedTextField(
            value = state.code,
            onValueChange = vm::onCodeChange,
            label = { Text(stringResource(R.string.onboarding_otp_hint)) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            isError = state.errorKey?.startsWith("verify_failed") == true,
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = vm::verifyOtp,
            enabled = !state.isLoading && state.code.length == 6,
            modifier = Modifier.fillMaxWidth().height(54.dp),
        ) {
            if (state.isLoading) CircularProgressIndicator(strokeWidth = 2.dp)
            else Text(stringResource(R.string.onboarding_verify))
        }
        Spacer(Modifier.height(8.dp))
        TextButton(onClick = vm::back) { Text("Use a different email") }
        TextButton(onClick = { vm.requestOtp(asLogin = state.purpose == "login") }) {
            Text(stringResource(R.string.onboarding_resend))
        }
        if (state.errorKey != null) {
            Spacer(Modifier.height(8.dp))
            Text(
                state.errorKey,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}
