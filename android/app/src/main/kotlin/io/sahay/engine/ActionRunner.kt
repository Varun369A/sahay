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
        // Once target app has been the foreground at least once, we enforce
        // that no other app steals focus mid-task (picture-in-picture,
        // notifications, system hijacks). Before target first appears,
        // the "launch" step is responsible for bringing it front.
        var sawTargetFg = false

        // CONFIRMATIONS REMOVED.
        // Safety still enforced by:
        //   1. PaymentBlocklist (every step + every executor call)
        //   2. The voice loop itself — user can stop with kill switch
        //      (volume-down 3x) at any time
        //   3. Backend tier limits + per-app allowlist
        for (step in plan.steps) {
            // Foreground hijack guard. Skip for launch steps — they bring the
            // app to foreground themselves. For every other step, if an app
            // OTHER THAN the target has stolen focus after we've seen target
            // foreground at least once, abort cleanly.
            if (step.action != "launch") {
                val currentFg = service.rootInActiveWindow?.packageName?.toString()
                if (currentFg == appPackage) {
                    sawTargetFg = true
                } else if (sawTargetFg && currentFg != null && currentFg != appPackage) {
                    android.util.Log.w(
                        "ActionRunner",
                        "fg_hijacked expected=$appPackage got=$currentFg step=${step.step}"
                    )
                    log(
                        appPackage, intent, "failed",
                        System.currentTimeMillis() - started,
                        failureReason = "foreground_hijacked_by:$currentFg"
                    )
                    return@withContext RunResult(
                        success = false,
                        reason = "foreground_hijacked_by:$currentFg"
                    )
                }
            }

            val outcome = executor.executeStep(step, appPackage)
            when (outcome) {
                is PlaybookExecutor.StepOutcome.Failed -> {
                    // Honor step.onFailure — conditional "if-on-this-screen" steps
                    // (profile picker, back-from-playback) MUST not abort the task.
                    val onFailure = step.onFailure?.lowercase() ?: "abort"
                    if (onFailure in OPTIONAL_ON_FAILURE) {
                        android.util.Log.i("ActionRunner",
                            "step_${step.step}_skipped reason=${outcome.reason} policy=$onFailure")
                        continue   // skip this step, move on to the next
                    }
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

    private companion object {
        /** Step.onFailure values that mean "step is conditional — skip on miss". */
        val OPTIONAL_ON_FAILURE = setOf("skip", "continue", "optional", "ignore")
    }
}
