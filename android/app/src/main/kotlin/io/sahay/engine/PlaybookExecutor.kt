package io.sahay.engine

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import io.sahay.data.api.PlannedStepOut
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Executes a list of PlannedStepOut against the live device using an
 * AccessibilityService instance.
 *
 * Hard rules:
 *   • Refuses if the foreground app is on the payment blocklist.
 *   • Caller (typically SahayForegroundService) is responsible for showing
 *     the ConfirmationActivity when step.confirmRequired is true.
 *   • Returns ExecutionResult per step so caller can stop on failure.
 */
class PlaybookExecutor(private val service: AccessibilityService) {

    sealed class StepOutcome {
        data object Success : StepOutcome()
        data class Failed(val reason: String) : StepOutcome()
        data object NeedsConfirmation : StepOutcome()
        data object Aborted : StepOutcome()
    }

    suspend fun executeStep(step: PlannedStepOut): StepOutcome {
        // Hard rule: payment blocklist check on every step.
        val rootPkg = service.rootInActiveWindow?.packageName?.toString()
        if (PaymentBlocklist.isBlocked(rootPkg)) {
            return StepOutcome.Aborted
        }

        return when (step.action) {
            "launch" -> launchApp(step)
            "tap" -> tap(step)
            "long_press" -> tap(step, longPress = true)
            "type" -> type(step)
            "back" -> { service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK); StepOutcome.Success }
            "home" -> { service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME); StepOutcome.Success }
            "wait" -> { delay(step.timeoutMs.toLong()); StepOutcome.Success }
            "verify" -> verify(step)
            "read" -> StepOutcome.Success    // no-op — caller can read tree separately
            "swipe" -> StepOutcome.Failed("swipe_not_implemented_v1")
            else -> StepOutcome.Failed("unknown_action:${step.action}")
        }
    }

    // ─── Action impls ────────────────────────────────────────────────

    private suspend fun launchApp(step: PlannedStepOut): StepOutcome {
        val pkg = (step.targetSelector["package"] as? JsonPrimitive)?.contentOrNull
            ?: return StepOutcome.Failed("missing_package")
        if (PaymentBlocklist.isBlocked(pkg)) return StepOutcome.Aborted
        val intent = service.packageManager.getLaunchIntentForPackage(pkg)
            ?: return StepOutcome.Failed("app_not_installed:$pkg")
        intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        service.startActivity(intent)
        delay(2000)   // give the app time to come up
        return StepOutcome.Success
    }

    private suspend fun tap(step: PlannedStepOut, longPress: Boolean = false): StepOutcome {
        val node = waitForNode(step) ?: return StepOutcome.Failed("node_not_found")
        val action = if (longPress) AccessibilityNodeInfo.ACTION_LONG_CLICK
                     else AccessibilityNodeInfo.ACTION_CLICK
        // Walk up to find a clickable ancestor if leaf isn't itself clickable.
        var target: AccessibilityNodeInfo? = node
        while (target != null && !target.isClickable) target = target.parent
        target ?: return StepOutcome.Failed("no_clickable_ancestor")
        val ok = target.performAction(action)
        return if (ok) StepOutcome.Success else StepOutcome.Failed("perform_action_returned_false")
    }

    private suspend fun type(step: PlannedStepOut): StepOutcome {
        val node = waitForNode(step) ?: return StepOutcome.Failed("node_not_found")
        val text = (step.params["text"] as? JsonPrimitive)?.contentOrNull
            ?: return StepOutcome.Failed("missing_text_param")
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        return if (ok) StepOutcome.Success else StepOutcome.Failed("set_text_failed")
    }

    private suspend fun verify(step: PlannedStepOut): StepOutcome {
        val node = waitForNode(step)
        return if (node != null) StepOutcome.Success else StepOutcome.Failed("verify_node_missing")
    }

    private suspend fun waitForNode(step: PlannedStepOut): AccessibilityNodeInfo? {
        val deadline = System.currentTimeMillis() + step.timeoutMs
        val selectors = step.targetSelector.mapValues { (it.value as? JsonPrimitive)?.contentOrNull }
        while (System.currentTimeMillis() < deadline) {
            val root = service.rootInActiveWindow
            ScreenTreeReader.findNode(root, selectors)?.let { return it }
            delay(200)
        }
        return null
    }
}
