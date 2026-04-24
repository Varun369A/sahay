package io.sahay.engine

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.util.DisplayMetrics
import android.util.Log
import android.view.WindowManager
import dagger.hilt.android.qualifiers.ApplicationContext
import io.sahay.data.api.AgentObservation
import io.sahay.data.api.AgentStepRequest
import io.sahay.data.api.AgentTool
import io.sahay.data.api.SahayApi
import io.sahay.service.SahayAccessibilityService
import io.sahay.voice.TtsClient
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The agent loop. Drives the phone screen-by-screen using Claude as the brain.
 *
 * run(goal):
 *   while not done and steps < MAX:
 *     1. Observe: screenshot + tree + foreground package
 *     2. Backend → Claude returns next tool call
 *     3. Execute tool (tap / type / swipe / launch / speak / done …)
 *     4. Wait for UI to settle
 *     5. Loop
 *
 * Safety:
 *   • Payment app blocklist enforced on every iteration (client + server)
 *   • Kill switch from the existing AccessibilityService still works
 *   • MAX_STEPS = 20 cap prevents infinite loops
 */
@Singleton
class AgentLoop @Inject constructor(
    @ApplicationContext private val context: Context,
    private val api: SahayApi,
    private val tts: TtsClient,
) {

    data class Outcome(
        val success: Boolean,
        val stepsUsed: Int,
        val reason: String? = null,
        val summary: String? = null,
    )

    /** Entry point. `goal` is the natural-language user utterance. */
    suspend fun run(goal: String): Outcome {
        val service = SahayAccessibilityService.instance()
            ?: return Outcome(false, 0, reason = "accessibility_service_not_running")

        tts.init()

        val (sw, sh) = screenSize()
        val history = mutableListOf<JsonObject>()

        for (step in 0 until MAX_STEPS) {
            if (PaymentBlocklist.isBlocked(service.rootInActiveWindow?.packageName?.toString())) {
                tts.speak("I can't work inside payment apps for your safety.")
                return Outcome(false, step, reason = "payment_app_foreground")
            }

            // 1. Observe
            val tree = ScreenTreeReader.summarize(service.rootInActiveWindow, maxNodes = 300)
            val shot = ScreenCapture.capture(service)
            val currentPkg = service.rootInActiveWindow?.packageName?.toString()

            val obs = AgentObservation(
                goal = goal,
                appPackage = currentPkg,
                screenTreeSummary = tree,
                screenshotBase64 = shot?.base64Png,
                stepIndex = step,
                history = history.toList(),
                screenWidth = shot?.width ?: sw,
                screenHeight = shot?.height ?: sh,
            )

            // 2. Ask Claude
            val resp = try {
                api.agentStep(AgentStepRequest(obs))
            } catch (t: Throwable) {
                Log.e(TAG, "agent_call_failed", t)
                return Outcome(false, step, reason = "agent_call_failed:${t.message}")
            }
            val action = resp.action
            Log.i(TAG, "step=$step tool=${action.tool} model=${resp.model} " +
                "reasoning=${action.reasoning ?: ""}")

            // 3. Execute
            val exec = execute(service, action)

            history += buildJsonObject {
                put("step", step)
                put("tool", action.tool)
                action.x?.let { put("x", it) }
                action.y?.let { put("y", it) }
                action.text?.let { put("text", it) }
                action.appPackage?.let { put("app_package", it) }
                put("outcome", exec.outcome)
                put("reasoning", action.reasoning ?: "")
            }

            when (exec.outcome) {
                "done" -> {
                    tts.speak(action.message ?: "Done.")
                    return Outcome(true, step + 1, summary = action.message)
                }
                "give_up" -> {
                    tts.speak(action.message ?: "I couldn't finish that.")
                    return Outcome(false, step + 1, reason = action.message)
                }
                "ask_user" -> {
                    tts.speak(action.message ?: "I need more info.")
                    return Outcome(false, step + 1, reason = "asked_user:${action.message}")
                }
            }

            // 4. Settle before next observation (unless the tool was wait itself)
            if (action.tool != "wait") delay(exec.settleMs)
        }

        tts.speak("I took too many steps — stopping for safety.")
        return Outcome(false, MAX_STEPS, reason = "max_steps_reached")
    }

    // ─── Execute one tool call ────────────────────────────────────────

    private data class ExecResult(val outcome: String, val settleMs: Long = 800)

    private suspend fun execute(
        service: AccessibilityService,
        action: AgentTool,
    ): ExecResult {
        return when (action.tool) {
            "tap", "long_press" -> tapOrLongPress(service, action)
            "type" -> typeText(service, action)
            "swipe" -> swipe(service, action)
            "wait" -> {
                delay((action.durationMs ?: 1000).toLong().coerceIn(50, 10_000))
                ExecResult("ok", settleMs = 0)
            }
            "back" -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
                ExecResult("ok")
            }
            "home" -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
                ExecResult("ok", settleMs = 1000)
            }
            "open_app" -> openApp(service, action)
            "speak" -> {
                tts.speak(action.message ?: "")
                ExecResult("ok", settleMs = 0)
            }
            "ask_user" -> ExecResult("ask_user", settleMs = 0)
            "done" -> ExecResult("done", settleMs = 0)
            "give_up" -> ExecResult("give_up", settleMs = 0)
            else -> ExecResult("unknown_tool:${action.tool}", settleMs = 0)
        }
    }

    private suspend fun tapOrLongPress(
        service: AccessibilityService, action: AgentTool,
    ): ExecResult {
        val x = action.x
        val y = action.y
        if (x != null && y != null) {
            val ok = if (action.tool == "long_press")
                GestureDispatcher.longPress(service, x, y)
            else GestureDispatcher.tap(service, x, y)
            return ExecResult(if (ok) "ok" else "gesture_rejected", settleMs = 900)
        }
        // Fall back to selector-based tap via existing executor logic
        val selectors = action.selector.mapValues { (it.value as? JsonPrimitive)?.content }
        val node = ScreenTreeReader.findNode(service.rootInActiveWindow, selectors)
        if (node != null) {
            var target: android.view.accessibility.AccessibilityNodeInfo? = node
            while (target != null && !target.isClickable) target = target.parent
            target?.performAction(
                if (action.tool == "long_press")
                    android.view.accessibility.AccessibilityNodeInfo.ACTION_LONG_CLICK
                else android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK
            )
            return ExecResult("ok", settleMs = 900)
        }
        return ExecResult("no_target")
    }

    private suspend fun typeText(
        service: AccessibilityService, action: AgentTool,
    ): ExecResult {
        val text = action.text ?: return ExecResult("missing_text")
        // Prefer currently focused node
        val focused = findFocusedEditable(service) ?: return ExecResult("no_focused_field")
        val args = android.os.Bundle().apply {
            putCharSequence(
                android.view.accessibility.AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        val ok = focused.performAction(
            android.view.accessibility.AccessibilityNodeInfo.ACTION_SET_TEXT, args
        )
        return ExecResult(if (ok) "ok" else "set_text_failed", settleMs = 600)
    }

    private fun findFocusedEditable(
        service: AccessibilityService,
    ): android.view.accessibility.AccessibilityNodeInfo? {
        val root = service.rootInActiveWindow ?: return null
        // BFS
        val stack = ArrayDeque<android.view.accessibility.AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (n.isEditable || n.isFocused) return n
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }
        return null
    }

    private suspend fun swipe(service: AccessibilityService, action: AgentTool): ExecResult {
        val (sw, sh) = screenSize()
        val cx = sw / 2
        val cy = sh / 2
        val dist = (action.distance ?: 500).coerceIn(50, 2000)
        val (fromX, fromY, toX, toY) = when (action.direction) {
            "up"    -> listOf(cx, cy + dist / 2, cx, cy - dist / 2)
            "down"  -> listOf(cx, cy - dist / 2, cx, cy + dist / 2)
            "left"  -> listOf(cx + dist / 2, cy, cx - dist / 2, cy)
            "right" -> listOf(cx - dist / 2, cy, cx + dist / 2, cy)
            else    -> return ExecResult("bad_direction")
        }
        val ok = GestureDispatcher.swipe(service, fromX, fromY, toX, toY)
        return ExecResult(if (ok) "ok" else "swipe_rejected", settleMs = 700)
    }

    private suspend fun openApp(service: AccessibilityService, action: AgentTool): ExecResult {
        val pkg = action.appPackage ?: return ExecResult("missing_app_package")
        if (PaymentBlocklist.isBlocked(pkg)) return ExecResult("payment_app_blocked")
        val intent = service.packageManager.getLaunchIntentForPackage(pkg)
            ?: return ExecResult("app_not_installed:$pkg")
        service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
        delay(400)
        intent.addFlags(
            android.content.Intent.FLAG_ACTIVITY_NEW_TASK
                or android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
        )
        service.startActivity(intent)
        // Poll for foreground
        val deadline = System.currentTimeMillis() + 10_000
        while (System.currentTimeMillis() < deadline) {
            val fg = service.rootInActiveWindow?.packageName?.toString()
            if (fg == pkg) return ExecResult("ok", settleMs = 1200)
            delay(250)
        }
        return ExecResult("launched_not_foregrounded:$pkg")
    }

    private fun screenSize(): Pair<Int, Int> {
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getRealMetrics(metrics)
        return metrics.widthPixels to metrics.heightPixels
    }

    private companion object {
        const val TAG = "AgentLoop"
        const val MAX_STEPS = 20
    }
}
