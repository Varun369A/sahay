package io.sahay.engine

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import io.sahay.data.api.PlannedStepOut
import io.sahay.data.api.ResolveRequest
import io.sahay.data.api.SahayApi
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Executes one PlannedStepOut against the live device.
 *
 * Strategy when target node isn't found by the playbook's fixed selector:
 *   1. Try fuzzy matching (text/contentDescription substring, case-insensitive)
 *   2. Fall back to backend `/v1/actions/resolve` — Claude reads the live
 *      accessibility tree and picks the right node
 *   3. Only fail if both miss
 *
 * This makes Sahay survive UI drift (Netflix changing viewIds) and
 * conditional screens (account picker, splash, login walls).
 */
class PlaybookExecutor(
    private val service: AccessibilityService,
    private val api: SahayApi,
) {

    sealed class StepOutcome {
        data object Success : StepOutcome()
        data class Failed(val reason: String) : StepOutcome()
        data object NeedsConfirmation : StepOutcome()
        data object Aborted : StepOutcome()
    }

    suspend fun executeStep(step: PlannedStepOut, appPackage: String): StepOutcome {
        val rootPkg = service.rootInActiveWindow?.packageName?.toString()
        if (PaymentBlocklist.isBlocked(rootPkg)) return StepOutcome.Aborted

        return when (step.action) {
            "launch" -> launchApp(step)
            "tap" -> tapWithFallback(step, appPackage, longPress = false)
            "long_press" -> tapWithFallback(step, appPackage, longPress = true)
            "type" -> typeWithFallback(step, appPackage)
            "back" -> { service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK); StepOutcome.Success }
            "home" -> { service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME); StepOutcome.Success }
            "wait" -> { delay(step.timeoutMs.toLong()); StepOutcome.Success }
            "verify" -> verify(step, appPackage)
            "read" -> StepOutcome.Success
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

        // 1. Press HOME first — if Sahay itself (or any other app) is in
        //    foreground, our subsequent startActivity doesn't reliably bring
        //    the target app to front on some OEMs. HOME clears the stack.
        val currentPkg = service.rootInActiveWindow?.packageName?.toString()
        if (currentPkg != pkg) {
            service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            delay(500)
        }

        // 2. Launch the target app in its own task.
        intent.addFlags(
            android.content.Intent.FLAG_ACTIVITY_NEW_TASK
                or android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
        )
        service.startActivity(intent)

        // 3. Poll until target app is actually the foreground window.
        //    Up to 10s for cold start (Netflix splash can take 5-8s).
        val deadline = System.currentTimeMillis() + 10_000
        while (System.currentTimeMillis() < deadline) {
            val fg = service.rootInActiveWindow?.packageName?.toString()
            if (fg == pkg) {
                android.util.Log.i(TAG, "launch_success pkg=$pkg")
                // Small settle delay for initial UI drawing.
                delay(800)
                return StepOutcome.Success
            }
            delay(250)
        }
        return StepOutcome.Failed("launched_but_not_foregrounded:$pkg")
    }

    private suspend fun tapWithFallback(
        step: PlannedStepOut,
        appPackage: String,
        longPress: Boolean,
    ): StepOutcome {
        val node = waitForNode(step) ?: askResolverFor(step, appPackage)
            ?: return StepOutcome.Failed("node_not_found")
        val action = if (longPress) AccessibilityNodeInfo.ACTION_LONG_CLICK
                     else AccessibilityNodeInfo.ACTION_CLICK
        var target: AccessibilityNodeInfo? = node
        while (target != null && !target.isClickable) target = target.parent
        target ?: return StepOutcome.Failed("no_clickable_ancestor")
        val ok = target.performAction(action)
        return if (ok) StepOutcome.Success else StepOutcome.Failed("perform_action_returned_false")
    }

    private suspend fun typeWithFallback(step: PlannedStepOut, appPackage: String): StepOutcome {
        val node = waitForNode(step) ?: askResolverFor(step, appPackage)
            ?: return StepOutcome.Failed("node_not_found")
        val text = (step.params["text"] as? JsonPrimitive)?.contentOrNull
            ?: return StepOutcome.Failed("missing_text_param")
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        return if (ok) StepOutcome.Success else StepOutcome.Failed("set_text_failed")
    }

    private suspend fun verify(step: PlannedStepOut, appPackage: String): StepOutcome {
        val node = waitForNode(step) ?: askResolverFor(step, appPackage)
        return if (node != null) StepOutcome.Success else StepOutcome.Failed("verify_node_missing")
    }

    // ─── Selector lookup ─────────────────────────────────────────────

    private suspend fun waitForNode(step: PlannedStepOut): AccessibilityNodeInfo? {
        val deadline = System.currentTimeMillis() + step.timeoutMs
        val selectors = step.targetSelector.mapValues {
            (it.value as? JsonPrimitive)?.contentOrNull
        }
        while (System.currentTimeMillis() < deadline) {
            val root = service.rootInActiveWindow
            ScreenTreeReader.findNode(root, selectors)?.let { return it }
            delay(200)
        }
        return null
    }

    /**
     * Vision-grounded fallback: ask backend Claude to pick the right node
     * from the live accessibility tree. Costs ~₹0.1, only fires on miss.
     */
    private suspend fun askResolverFor(
        step: PlannedStepOut,
        appPackage: String,
    ): AccessibilityNodeInfo? {
        val root = service.rootInActiveWindow ?: return null
        val tree = ScreenTreeReader.summarize(root, maxNodes = 250)

        val resolved = try {
            api.resolveStep(
                ResolveRequest(
                    appPackage = appPackage,
                    stepDescription = step.description,
                    desiredAction = step.action,
                    screenTreeSummary = tree,
                    inputs = JsonObject(step.params),
                )
            )
        } catch (t: Throwable) {
            Log.e(TAG, "resolve_call_failed", t)
            return null
        }

        if (!resolved.found) {
            Log.i(TAG, "resolver_no_match step=${step.description} notes=${resolved.notes}")
            return null
        }

        val newSelectors = resolved.selector.mapValues { it.value.jsonPrimitive.contentOrNull }
        Log.i(TAG, "resolver_found selectors=$newSelectors conf=${resolved.confidence}")
        return ScreenTreeReader.findNode(root, newSelectors)
    }

    private companion object {
        const val TAG = "PlaybookExec"
    }
}
