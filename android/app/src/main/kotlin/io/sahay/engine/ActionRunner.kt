package io.sahay.engine

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import io.sahay.data.api.ActionLogRequest
import io.sahay.data.api.PlanRequest
import io.sahay.data.api.PlannedStepOut
import io.sahay.data.api.SahayApi
import io.sahay.service.SahayAccessibilityService
import io.sahay.ui.screens.confirm.ConfirmationActivity
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonObject

/**
 * Orchestrates one user-issued action end to end:
 *   1. Block payment apps
 *   2. Call /v1/actions/plan to get steps
 *   3. For each step: confirm if needed, then execute via accessibility service
 *   4. POST /v1/actions/log with outcome
 *
 * Runs inside the SahayForegroundService coroutine scope.
 */
@Singleton
class ActionRunner @Inject constructor(
    @ApplicationContext private val context: Context,
    private val api: SahayApi,
    private val json: Json,
) {

    suspend fun run(appPackage: String, intent: String, inputsJsonStr: String): RunResult = withContext(Dispatchers.Default) {
        // 1. Hard block payment apps (defense in depth — server also blocks).
        if (PaymentBlocklist.isBlocked(appPackage)) {
            return@withContext RunResult(success = false, reason = "payment_app_blocked")
        }

        // 2. Plan
        val inputs: Map<String, JsonElement> = runCatching {
            json.parseToJsonElement(inputsJsonStr).jsonObject.toMap()
        }.getOrDefault(emptyMap())

        val plan = runCatching {
            api.planAction(
                PlanRequest(
                    appPackage = appPackage,
                    intent = intent,
                    inputs = inputs,
                    screenTreeSummary = currentScreenSummary(),
                )
            )
        }.getOrElse {
            return@withContext RunResult(success = false, reason = "plan_failed:${it.message}")
        }

        if (plan.requiresInputs.isNotEmpty()) {
            return@withContext RunResult(
                success = false,
                reason = "missing_inputs:${plan.requiresInputs.joinToString(",")}",
            )
        }

        // 3. Execute step by step.
        val service = SahayAccessibilityService.instance()
            ?: return@withContext RunResult(success = false, reason = "accessibility_service_not_running")
        val executor = service.executor()
            ?: return@withContext RunResult(success = false, reason = "executor_unavailable")

        val started = System.currentTimeMillis()
        for (step in plan.steps) {
            // Only confirm steps the playbook itself flagged as destructive
            // (Send / Post / Save / Pay-style). Earlier blanket rule on
            // low-reliability is gone — it was causing N confirmations per
            // task, killing UX. Reliability scoring still applies elsewhere.
            if (step.confirmRequired) {
                val approved = ConfirmationActivity.requestApproval(context, step)
                if (!approved) {
                    log(appPackage, intent, "aborted", System.currentTimeMillis() - started, userAborted = true)
                    return@withContext RunResult(success = false, reason = "user_aborted")
                }
            }

            val outcome = executor.executeStep(step, appPackage)
            when (outcome) {
                is PlaybookExecutor.StepOutcome.Failed -> {
                    log(appPackage, intent, "failed", System.currentTimeMillis() - started, failureReason = outcome.reason)
                    return@withContext RunResult(success = false, reason = outcome.reason)
                }
                PlaybookExecutor.StepOutcome.Aborted -> {
                    log(appPackage, intent, "aborted", System.currentTimeMillis() - started, userAborted = false, failureReason = "blocklist_or_kill_switch")
                    return@withContext RunResult(success = false, reason = "aborted")
                }
                PlaybookExecutor.StepOutcome.NeedsConfirmation -> {
                    // Should have been caught above — treat as abort.
                    return@withContext RunResult(success = false, reason = "needs_confirm_unhandled")
                }
                PlaybookExecutor.StepOutcome.Success -> Unit
            }
        }

        log(appPackage, intent, "success", System.currentTimeMillis() - started)
        RunResult(success = true)
    }

    private suspend fun log(
        appPackage: String,
        intent: String,
        status: String,
        durationMs: Long,
        userAborted: Boolean = false,
        failureReason: String? = null,
    ) {
        runCatching {
            api.logAction(
                ActionLogRequest(
                    appPackage = appPackage,
                    intent = intent,
                    status = status,
                    durationMs = durationMs.toInt(),
                    failureReason = failureReason,
                    userAborted = userAborted,
                )
            )
        }
    }

    private fun currentScreenSummary(): String? {
        val service = SahayAccessibilityService.instance() ?: return null
        return ScreenTreeReader.summarize(service.rootInActiveWindow)
    }

    data class RunResult(val success: Boolean, val reason: String? = null)
}
