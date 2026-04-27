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
import io.sahay.data.secrets.UserPreferences
import io.sahay.service.SahayAccessibilityService
import io.sahay.voice.TtsClient
import java.util.UUID
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
    private val prefs: UserPreferences,
    private val idempotency: IdempotencyTracker,
    private val pendingClarification: PendingClarification,
) {

    data class Outcome(
        val success: Boolean,
        val stepsUsed: Int,
        val reason: String? = null,
        val summary: String? = null,
    )

    /**
     * Entry point. `goal` is the natural-language user utterance.
     *
     * One call = one conversation. A fresh UUID scopes the PII token map
     * and turn memory on the backend so tasks can't leak state into each
     * other. For multi-turn dialogues (future), the caller can reuse a
     * conversation_id by calling a future overload.
     */
    /** Start a fresh task from scratch — new conversation, no prior context. */
    suspend fun run(goal: String): Outcome =
        runInternal(goal = goal, resumeConversationId = null, resumeReply = null)

    /**
     * Resume a paused conversation with the user's spoken answer to the
     * agent's previous `ask_user`. The agent reads the reply naturally
     * via conversation memory — no fresh "what's my goal?" reasoning,
     * no loss of the original task context.
     *
     *   • `conversationId` — the id from the paused run (held by
     *     PendingClarification).
     *   • `originalGoal`   — same goal as the original run; used for
     *     prompt context if the server's Redis has been evicted.
     *   • `userReply`      — what the user just said in response to the
     *     question. Sent ONCE on the first server step of the resumed
     *     loop, then cleared.
     */
    suspend fun resume(
        conversationId: String,
        originalGoal: String,
        userReply: String,
    ): Outcome =
        runInternal(
            goal = originalGoal,
            resumeConversationId = conversationId,
            resumeReply = userReply,
        )

    private suspend fun runInternal(
        goal: String,
        resumeConversationId: String?,
        resumeReply: String?,
    ): Outcome {
        val service = SahayAccessibilityService.instance()
            ?: return Outcome(false, 0, reason = "accessibility_service_not_running")

        tts.init()

        // Resume reuses the prior conversation id so the server-side
        // memory + plan stay aligned. Fresh runs always get a new id.
        val isResume = resumeConversationId != null
        val conversationId = resumeConversationId ?: UUID.randomUUID().toString()
        val snap = prefs.snapshot()
        val assistantName = snap.assistantName
        val screenshotUploadEnabled = snap.screenshotUploadEnabled

        // Push Sahay off-screen so the agent doesn't see our own "Running:"
        // text and get confused into thinking the prior task is still going.
        val currentPkg = service.rootInActiveWindow?.packageName?.toString()
        if (currentPkg == context.packageName) {
            service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            delay(600)
        }

        val (sw, sh) = screenSize()
        val history = mutableListOf<JsonObject>()
        // Resume runs start at step_index=1 so server-side first-turn
        // logic (goal storage, goal rewriter) doesn't fire again.
        val startStep = if (isResume) 1 else 0

        for (step in startStep until MAX_STEPS) {
            if (PaymentBlocklist.isBlocked(service.rootInActiveWindow?.packageName?.toString())) {
                tts.speak("I can't work inside payment apps for your safety.")
                return Outcome(false, step, reason = "payment_app_foreground")
            }

            // 1. Observe
            // Privacy: screenshot upload is OFF by default. Tree-only mode keeps
            // the README's "screen content never leaves the device" claim true.
            // The user can opt in via Settings → "Send screenshots to assist".
            val tree = ScreenTreeReader.summarize(service.rootInActiveWindow, maxNodes = 300)
            val shot = if (screenshotUploadEnabled) ScreenCapture.capture(service) else null
            // Even with the toggle on, never upload a screenshot of a payment app
            // (the foreground-package check above already returns, but defence in depth).
            val currentPkg = service.rootInActiveWindow?.packageName?.toString()
            val safeShot = if (PaymentBlocklist.isBlocked(currentPkg)) null else shot

            val (obsW, obsH) = if (safeShot != null) safeShot.width to safeShot.height
                               else sw to sh

            // Send userReply on the FIRST iteration of a resume only.
            // Server engine appends it as a Turn in conversation memory
            // before invoking Claude — agent reads the reply naturally.
            val replyForThisStep =
                if (isResume && step == startStep) resumeReply else null

            val obs = AgentObservation(
                goal = goal,
                conversationId = conversationId,
                appPackage = currentPkg,
                screenTreeSummary = tree,
                screenshotBase64 = safeShot?.base64Png,
                stepIndex = step,
                history = history.toList(),
                screenWidth = obsW,
                screenHeight = obsH,
                assistantName = assistantName,
                userReply = replyForThisStep,
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

            // 2b. First-action ack — on step 0, if the agent's first move is
            // a device tool (not already a speak/ask_user), give the user a
            // short mood-aware phrase BEFORE dispatching so silence doesn't
            // read as "app is broken". Mood comes from the backend profile.
            if (step == 0 && action.tool !in SILENT_FIRST_TOOLS) {
                pickFirstAck(resp.mood)?.let { tts.announce(it, whisper = false) }
            }

            // 3. Execute
            val exec = execute(service, action, conversationId)

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
                    // Task closed cleanly — drop any pending clarification
                    // so a stale earlier question can't hijack a future
                    // utterance.
                    pendingClarification.clear()
                    return Outcome(true, step + 1, summary = action.message)
                }
                "give_up" -> {
                    tts.speak(action.message ?: "I couldn't finish that.")
                    pendingClarification.clear()
                    return Outcome(false, step + 1, reason = action.message)
                }
                "ask_user" -> {
                    val question = action.message ?: "I need more info."
                    tts.speak(question)
                    // Stash the conversation so the user's next utterance
                    // resumes THIS task instead of starting a new one.
                    pendingClarification.set(
                        conversationId = conversationId,
                        originalGoal = goal,
                        askedQuestion = question,
                    )
                    return Outcome(false, step + 1, reason = "asked_user:$question")
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
        conversationId: String,
    ): ExecResult {
        return when (action.tool) {
            "tap_node" -> tapIndexedNode(service, action)
            "type_in_node" -> typeInIndexedNode(service, action)
            "long_press_node" -> longPressIndexedNode(service, action)
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
            "reply_to_notification" -> replyToNotification(service, action, conversationId)
            "ask_user" -> ExecResult("ask_user", settleMs = 0)
            "done" -> ExecResult("done", settleMs = 0)
            "give_up" -> ExecResult("give_up", settleMs = 0)
            else -> ExecResult("unknown_tool:${action.tool}", settleMs = 0)
        }
    }

    /**
     * Fire the RemoteInput reply action stored on a still-active notification.
     * The agent has already walked the user through a confirm-then-send flow
     * server-side, so by the time we get here the text is final.
     *
     * We speak a short confirmation so the user gets audible feedback the
     * message left the phone — matches the "slow, steady, human tone" the
     * rest of the proactive flow uses.
     */
    private suspend fun replyToNotification(
        service: AccessibilityService, action: AgentTool, conversationId: String,
    ): ExecResult {
        val key = action.notificationKey.orEmpty()
        val text = action.text.orEmpty()
        if (key.isBlank() || text.isBlank()) return ExecResult("missing_reply_args")

        // Dedup: a network blip / agent retry must NOT cause WhatsApp to
        // send the same message twice. The key is stable for "this exact
        // reply, in this conversation" so a deliberate "send again" in a
        // new conversation still works.
        val idempKey = IdempotencyTracker.keyFor(
            "reply_to_notification", conversationId, key, text,
        )
        if (!idempotency.checkAndMark(idempKey)) {
            android.util.Log.w(TAG, "duplicate_reply_suppressed key=${key.take(12)}")
            tts.speak("Already sent that one.")
            return ExecResult("duplicate_skipped", settleMs = 0)
        }

        val outcome = io.sahay.service.NotificationReplyDispatcher.dispatch(
            service, keyPrefix = key, text = text,
        )
        val contact = action.contact.orEmpty()
        val spoken = when (outcome) {
            "ok" -> if (contact.isNotBlank()) "Sent to $contact." else "Reply sent."
            "notification_not_found" ->
                "I couldn't find that conversation any more — it may have been cleared."
            "payment_app_blocked" ->
                "I don't send messages into payment apps."
            "listener_not_running" ->
                "Notification access isn't active — enable it in Settings."
            "no_reply_action", "no_actions", "no_remote_input" ->
                "That app doesn't support inline replies."
            else -> "I couldn't send that reply."
        }
        tts.speak(spoken)
        return ExecResult(outcome, settleMs = 0)
    }

    private suspend fun tapIndexedNode(
        service: AccessibilityService, action: AgentTool,
    ): ExecResult {
        val i = action.nodeIndex ?: return ExecResult("missing_node_index")
        val node = ScreenTreeReader.nodeForIndex(i) ?: return ExecResult("unknown_node:$i")
        // Tap the real on-screen center — no guessing.
        val ok = GestureDispatcher.tap(service, node.centerX, node.centerY)
        Log.i(TAG, "tap_node i=$i at (${node.centerX},${node.centerY}) text='${node.text}' cd='${node.contentDescription}' ok=$ok")
        return ExecResult(if (ok) "ok" else "gesture_rejected", settleMs = 900)
    }

    private suspend fun longPressIndexedNode(
        service: AccessibilityService, action: AgentTool,
    ): ExecResult {
        val i = action.nodeIndex ?: return ExecResult("missing_node_index")
        val node = ScreenTreeReader.nodeForIndex(i) ?: return ExecResult("unknown_node:$i")
        val holdMs = (action.durationMs ?: 1200).toLong().coerceIn(300, 30_000)
        val path = android.graphics.Path().apply { moveTo(node.centerX.toFloat(), node.centerY.toFloat()) }
        val stroke = android.accessibilityservice.GestureDescription.StrokeDescription(path, 0, holdMs)
        val gesture = android.accessibilityservice.GestureDescription.Builder().addStroke(stroke).build()
        val ok = kotlinx.coroutines.suspendCancellableCoroutine<Boolean> { cont ->
            val accepted = service.dispatchGesture(
                gesture,
                object : AccessibilityService.GestureResultCallback() {
                    override fun onCompleted(d: android.accessibilityservice.GestureDescription?) {
                        if (cont.isActive) cont.resume(true) {}
                    }
                    override fun onCancelled(d: android.accessibilityservice.GestureDescription?) {
                        if (cont.isActive) cont.resume(false) {}
                    }
                },
                null,
            )
            if (!accepted && cont.isActive) cont.resume(false) {}
        }
        Log.i(TAG, "long_press_node i=$i at (${node.centerX},${node.centerY}) hold=${holdMs}ms ok=$ok")
        return ExecResult(if (ok) "ok" else "gesture_rejected", settleMs = 900)
    }

    private suspend fun typeInIndexedNode(
        service: AccessibilityService, action: AgentTool,
    ): ExecResult {
        val i = action.nodeIndex ?: return ExecResult("missing_node_index")
        val text = action.text ?: return ExecResult("missing_text")
        val indexed = ScreenTreeReader.nodeForIndex(i) ?: return ExecResult("unknown_node:$i")
        val target = indexed.node
        // Focus first so the keyboard opens on the right field.
        target.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_FOCUS)
        val args = android.os.Bundle().apply {
            putCharSequence(
                android.view.accessibility.AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        val ok = target.performAction(
            android.view.accessibility.AccessibilityNodeInfo.ACTION_SET_TEXT, args
        )
        Log.i(TAG, "type_in_node i=$i text='${text.take(40)}' ok=$ok")
        return ExecResult(if (ok) "ok" else "set_text_failed", settleMs = 700)
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

    /**
     * Tools where the agent is already about to speak or ask the user —
     * a redundant ack on top would just talk over Claude's own utterance.
     */
    private val SILENT_FIRST_TOOLS = setOf(
        "speak", "ask_user", "done", "give_up", "wait",
    )

    /**
     * Small, non-repeating phrase pool per mood. The agent speaks this
     * ONCE at step 0 so the user hears a voice immediately after asking
     * for something, rather than a silent 1–2 seconds while Sahay walks
     * the screen. Returns null for `focused` — that mood wants silence.
     */
    private fun pickFirstAck(mood: String?): String? {
        val pool = when ((mood ?: "default").lowercase()) {
            "focused" -> return null                    // silent by design
            "warm"    -> WARM_ACKS
            "playful" -> PLAYFUL_ACKS
            "formal"  -> FORMAL_ACKS
            else      -> DEFAULT_ACKS
        }
        return pool.random()
    }

    private companion object {
        const val TAG = "AgentLoop"
        const val MAX_STEPS = 20

        // Tuned by ear on Google TTS / Samsung TTS at pitch 0.9, rate 0.88.
        // Keep each under ~4 words — short enough that the actual tool
        // dispatches while the ack finishes playing.
        val DEFAULT_ACKS = listOf("On it.", "Got it.", "Sure.", "One sec.")
        val WARM_ACKS = listOf(
            "Got you.", "On it — one sec.", "Sure thing.", "Happy to.",
        )
        val PLAYFUL_ACKS = listOf(
            "On it.", "Consider it done.", "Easy.", "Watch this.",
        )
        val FORMAL_ACKS = listOf(
            "Right away.", "Certainly.", "Understood.",
        )
    }
}
