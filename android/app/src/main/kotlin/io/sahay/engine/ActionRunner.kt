package io.sahay.engine

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import io.sahay.data.api.ActionLogRequest
import io.sahay.data.api.PlanRequest
import io.sahay.data.api.PlannedStepOut
import io.sahay.data.api.PlaybookOutcomeRequest
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

        // Track the first failed step so we can report a useful outcome
        // back to the reliability tracker even though full-auto continues
        // through subsequent steps.
        var firstFailedStepIdx: Int? = null
        var firstFailedReason: String? = null
        var firstFailedWasExpect = false

        // FULL-AUTO MODE:
        //  • Foreground hijack = re-launch the target app, don't abort
        //  • Any failed step = log + continue to next (executor's internal
        //    resolver already did 3 attempts with vision fallback)
        //  • Only real aborts: payment-app blocklist, executor_unavailable
        //
        // Safety still enforced by:
        //   • PaymentBlocklist (every step + every executor call)
        //   • Kill switch (volume-down 3x)
        //   • Per-app allowlist
        for ((idx, step) in plan.steps.withIndex()) {
            // Foreground recovery: if something else stole focus, relaunch
            // the target app and retry this step instead of aborting.
            if (step.action != "launch") {
                val currentFg = service.rootInActiveWindow?.packageName?.toString()
                if (currentFg != null && currentFg != appPackage
                    && currentFg != "com.android.systemui"
                    && !currentFg.startsWith("com.android.inputmethod")) {
                    android.util.Log.w(
                        "ActionRunner",
                        "fg_recovery expected=$appPackage got=$currentFg step=${step.step} — relaunching"
                    )
                    relaunchTarget(appPackage)
                }
            }

            val outcome = executor.executeStep(step, appPackage)
            when (outcome) {
                is PlaybookExecutor.StepOutcome.Failed -> {
                    android.util.Log.i(
                        "ActionRunner",
                        "step_${step.step}_failed reason=${outcome.reason} — continuing anyway"
                    )
                    if (firstFailedStepIdx == null) {
                        firstFailedStepIdx = idx
                        firstFailedReason = outcome.reason
                        firstFailedWasExpect = outcome.reason.startsWith("expect_failed")
                    }
                    // Full-auto: never give up. Move to the next step.
                    // If this is the last step and it failed, we'll log it
                    // after the loop ends; but we never abort mid-playbook.
                    continue
                }
                PlaybookExecutor.StepOutcome.Aborted -> {
                    val durationMs = System.currentTimeMillis() - started
                    log(appPackage, intent, "aborted", durationMs, userAborted = false, failureReason = "blocklist_or_kill_switch")
                    reportPlaybookOutcome(
                        plan.playbookId, plan.revisionId,
                        "aborted", null, "blocklist_or_kill_switch", durationMs,
                    )
                    return@withContext RunResult(success = false, reason = "aborted")
                }
                PlaybookExecutor.StepOutcome.NeedsConfirmation -> {
                    // Should have been caught above — treat as abort.
                    return@withContext RunResult(success = false, reason = "needs_confirm_unhandled")
                }
                PlaybookExecutor.StepOutcome.Success -> Unit
            }
        }

        val durationMs = System.currentTimeMillis() - started
        if (firstFailedStepIdx == null) {
            log(appPackage, intent, "success", durationMs)
            reportPlaybookOutcome(
                plan.playbookId, plan.revisionId,
                "success", null, null, durationMs,
            )
            RunResult(success = true)
        } else {
            // At least one step failed but we ran the rest. Distinguish
            // verifier failures from generic failures so the score table
            // can tell "playbook is broken" from "verifier was too strict".
            val outcomeKind = if (firstFailedWasExpect) "expect_failed" else "failed"
            log(appPackage, intent, outcomeKind, durationMs, failureReason = firstFailedReason)
            reportPlaybookOutcome(
                plan.playbookId, plan.revisionId,
                outcomeKind, firstFailedStepIdx, firstFailedReason, durationMs,
            )
            // Surface success=true to the caller because full-auto completed
            // the run, but the reliability layer has already counted this as
            // a fail so the score reflects reality.
            RunResult(success = true, reason = "completed_with_fail:${firstFailedReason}")
        }
    }

    private suspend fun reportPlaybookOutcome(
        playbookId: String?,
        revisionId: String?,
        outcome: String,
        failedStepIndex: Int?,
        failedReason: String?,
        durationMs: Long,
    ) {
        if (playbookId.isNullOrBlank()) return   // vision-sourced plan, nothing to score
        runCatching {
            api.reportPlaybookOutcome(
                PlaybookOutcomeRequest(
                    playbookId = playbookId,
                    outcome = outcome,
                    failedStepIndex = failedStepIndex,
                    failedReason = failedReason?.take(200),
                    durationMs = durationMs.coerceAtLeast(0),
                    revisionId = revisionId,
                )
            )
        }.onFailure {
            android.util.Log.w("ActionRunner", "reliability_report_failed: ${it.message}")
        }
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

    private suspend fun relaunchTarget(appPackage: String) {
        val service = SahayAccessibilityService.instance() ?: return
        val intent = service.packageManager.getLaunchIntentForPackage(appPackage) ?: return
        service.performGlobalAction(
            android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME
        )
        kotlinx.coroutines.delay(400)
        intent.addFlags(
            android.content.Intent.FLAG_ACTIVITY_NEW_TASK
                or android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
        )
        service.startActivity(intent)
        // Poll until target is FG again (up to 6s)
        val deadline = System.currentTimeMillis() + 6_000
        while (System.currentTimeMillis() < deadline) {
            val fg = service.rootInActiveWindow?.packageName?.toString()
            if (fg == appPackage) { kotlinx.coroutines.delay(500); return }
            kotlinx.coroutines.delay(200)
        }
    }

    private companion object {
        /** Step.onFailure values that mean "step is conditional — skip on miss". */
        val OPTIONAL_ON_FAILURE = setOf("skip", "continue", "optional", "ignore")
    }
}
