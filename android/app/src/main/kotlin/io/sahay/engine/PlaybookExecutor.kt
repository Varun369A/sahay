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

    /**
     * Per-step record of which selector tier won the match. Populated by
     * `tapWithFallback` / `typeWithFallback` from `ScreenTreeReader.lastWinningTier`
     * after each successful selector resolution. The ActionRunner reads
     * this map at the end of a run and ships it to the backend for the
     * selector_trainer pipeline.
     *
     * Cleared on each new run via `resetSelectorTiers()` to avoid leaking
     * tier wins across playbook executions.
     */
    private val selectorTiers = mutableMapOf<Int, String>()

    fun resetSelectorTiers() { selectorTiers.clear() }
    fun selectorTiersSnapshot(): Map<Int, String> = selectorTiers.toMap()

    suspend fun executeStep(step: PlannedStepOut, appPackage: String): StepOutcome {
        val rootPkg = service.rootInActiveWindow?.packageName?.toString()
        if (PaymentBlocklist.isBlocked(rootPkg)) return StepOutcome.Aborted

        // Resolve via the canonical enum so a typo in the playbook (or a
        // backend rename that didn't propagate) becomes a loud, traceable
        // failure instead of a silent no-op.
        val action = PlaybookAction.fromWire(step.action)
            ?: return StepOutcome.Failed("unknown_action:${step.action}")

        // Snapshot the screen BEFORE the action so a `screen_changed`
        // expectation has something to compare against. Only computed when
        // an expectation needs it.
        val beforeHash =
            if (step.expect?.let { ExpectKind.fromWire(it.kind) } == ExpectKind.SCREEN_CHANGED)
                StepVerifier.snapshotHash(service.rootInActiveWindow)
            else null

        val outcome = when (action) {
            PlaybookAction.LAUNCH      -> launchApp(step)
            PlaybookAction.TAP         -> tapWithFallback(step, appPackage, longPress = false)
            PlaybookAction.LONG_PRESS  -> tapWithFallback(step, appPackage, longPress = true)
            PlaybookAction.TYPE        -> typeWithFallback(step, appPackage)
            PlaybookAction.SWIPE       -> swipe(step)
            PlaybookAction.WAIT        -> { delay(step.timeoutMs.toLong()); StepOutcome.Success }
            PlaybookAction.VERIFY      -> verify(step, appPackage)
            PlaybookAction.READ        -> StepOutcome.Success
            PlaybookAction.BACK        -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
                StepOutcome.Success
            }
            PlaybookAction.HOME        -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
                StepOutcome.Success
            }
        }

        // Verify post-condition. Only runs when the action itself didn't
        // already fail — no point checking expectations against a step we
        // know didn't happen.
        val expect = step.expect
        if (outcome is StepOutcome.Success && expect != null) {
            val result = StepVerifier.verify(service, expect, beforeHash = beforeHash)
            if (!result.passed) {
                Log.w(TAG, "step_expect_failed step=${step.step} kind=${expect.kind}")
                return StepOutcome.Failed("expect_failed:${result.reason}")
            }
        }
        return outcome
    }

    /**
     * Implements the playbook `swipe` action. Reads `params.direction`
     * (up|down|left|right) and optional `params.distance` (px, default 500).
     * Falls back to a vertical scroll if direction is missing/unknown so
     * generic "scroll down to find X" steps still make progress.
     */
    private suspend fun swipe(step: PlannedStepOut): StepOutcome {
        val direction = (step.params["direction"] as? JsonPrimitive)?.contentOrNull?.lowercase()
        val dist = ((step.params["distance"] as? JsonPrimitive)?.contentOrNull?.toIntOrNull()
            ?: 500).coerceIn(50, 2000)
        val metrics = service.resources.displayMetrics
        val cx = metrics.widthPixels / 2
        val cy = metrics.heightPixels / 2
        val (fromX, fromY, toX, toY) = when (direction) {
            "up"    -> listOf(cx, cy + dist / 2, cx, cy - dist / 2)
            "down"  -> listOf(cx, cy - dist / 2, cx, cy + dist / 2)
            "left"  -> listOf(cx + dist / 2, cy, cx - dist / 2, cy)
            "right" -> listOf(cx - dist / 2, cy, cx + dist / 2, cy)
            null    -> listOf(cx, cy + dist / 2, cx, cy - dist / 2)  // default: scroll down
            else    -> return StepOutcome.Failed("swipe_bad_direction:$direction")
        }
        val ok = GestureDispatcher.swipe(service, fromX, fromY, toX, toY)
        return if (ok) StepOutcome.Success else StepOutcome.Failed("swipe_rejected")
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
        // Confidence-floor: never let `anyLikelyCandidate` guess on
        // irreversible actions (send / delete / share / pay-adjacent).
        // For those, only exact selector or backend-resolver matches are allowed.
        val guess = if (step.confirmRequired) null else anyLikelyCandidate(step)
        val (node, tier) = run {
            waitForNode(step)?.let { it to (ScreenTreeReader.lastWinningTier ?: "selector") }
                ?: askResolverFor(step, appPackage)?.let { it to "resolver" }
                ?: guess?.let { it to "fuzzy_keyword" }
                ?: return StepOutcome.Failed(
                    if (step.confirmRequired) "node_not_found_strict_confirm_required"
                    else "node_not_found"
                )
        }
        selectorTiers[step.step] = tier
        val action = if (longPress) AccessibilityNodeInfo.ACTION_LONG_CLICK
                     else AccessibilityNodeInfo.ACTION_CLICK
        var target: AccessibilityNodeInfo? = node
        while (target != null && !target.isClickable) target = target.parent
        target ?: return StepOutcome.Failed("no_clickable_ancestor")
        val ok = target.performAction(action)
        return if (ok) StepOutcome.Success else StepOutcome.Failed("perform_action_returned_false")
    }

    private suspend fun typeWithFallback(step: PlannedStepOut, appPackage: String): StepOutcome {
        // Confidence-floor: typing into a guessed field on a confirm-required step
        // could put the user's text into the wrong app's input. Strict on those.
        val guessField = if (step.confirmRequired) null else anyEditText()
        val (node, tier) = run {
            waitForNode(step)?.let { it to (ScreenTreeReader.lastWinningTier ?: "selector") }
                ?: askResolverFor(step, appPackage)?.let { it to "resolver" }
                ?: guessField?.let { it to "fuzzy_edittext" }
                ?: return StepOutcome.Failed(
                    if (step.confirmRequired) "node_not_found_strict_confirm_required"
                    else "node_not_found"
                )
        }
        selectorTiers[step.step] = tier
        val text = (step.params["text"] as? JsonPrimitive)?.contentOrNull
            ?: return StepOutcome.Failed("missing_text_param")
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        // Focus the field first, then set text.
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        return if (ok) StepOutcome.Success else StepOutcome.Failed("set_text_failed")
    }

    /**
     * Last-ditch clickable finder — any clickable node matching the step's description keywords.
     *
     * Confidence-floor rules (added to prevent wrong-clicks):
     *   1. The winning node must match at least MIN_KEYWORD_HITS keywords.
     *   2. The winner must beat the runner-up by at least MIN_LEAD hits — a tied
     *      score means the screen has multiple plausible targets and we must NOT
     *      silently pick one.
     *   3. The candidate keyword pool must itself be specific enough
     *      (>= MIN_DISTINCT_KEYWORDS distinct words). One generic keyword like
     *      "open" is not a license to click whatever has "open" in its text.
     * If any check fails we return null — caller treats this as a hard miss.
     */
    private fun anyLikelyCandidate(step: PlannedStepOut): AccessibilityNodeInfo? {
        val root = service.rootInActiveWindow ?: return null
        val keywords = extractKeywords(step)
        if (keywords.size < MIN_DISTINCT_KEYWORDS) {
            Log.i(TAG, "anyLikelyCandidate rejected — too few keywords (${keywords.size})")
            return null
        }

        // Walk the tree, score each clickable node by keyword overlap.
        // Track the runner-up so we can require a clear winner.
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        var best: AccessibilityNodeInfo? = null
        var bestScore = 0
        var runnerUpScore = 0
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (n.isClickable || n.isFocusable) {
                val text = (n.text?.toString() ?: "") + " " +
                           (n.contentDescription?.toString() ?: "")
                val score = keywords.count { text.contains(it, ignoreCase = true) }
                when {
                    score > bestScore -> {
                        runnerUpScore = bestScore
                        bestScore = score
                        best = n
                    }
                    score == bestScore && best != null && n != best -> {
                        runnerUpScore = score
                    }
                    score in (runnerUpScore + 1)..bestScore -> {
                        runnerUpScore = score
                    }
                }
            }
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }

        if (best == null || bestScore < MIN_KEYWORD_HITS) {
            Log.i(TAG, "anyLikelyCandidate rejected — best=$bestScore floor=$MIN_KEYWORD_HITS")
            return null
        }
        if (bestScore - runnerUpScore < MIN_LEAD) {
            Log.i(TAG, "anyLikelyCandidate rejected — ambiguous best=$bestScore runner=$runnerUpScore")
            return null
        }
        Log.i(TAG, "anyLikelyCandidate picked score=$bestScore (runner=$runnerUpScore) text='${best.text}'")
        return best
    }

    /** Last-ditch text-input finder — first EditText-like node on screen. */
    private fun anyEditText(): AccessibilityNodeInfo? {
        val root = service.rootInActiveWindow ?: return null
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (n.isEditable ||
                n.className?.toString()?.contains("EditText", ignoreCase = true) == true) {
                Log.i(TAG, "anyEditText found cls=${n.className}")
                return n
            }
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }
        return null
    }

    private fun extractKeywords(step: PlannedStepOut): List<String> {
        val parts = mutableListOf<String>()
        // From description — first few meaningful words.
        step.description.split(" ", ",", "(", ")", "—")
            .map { it.trim().lowercase() }
            .filter { it.length > 2 && it !in STOPWORDS }
            .take(3).let { parts.addAll(it) }
        // From params — any {{x}} values already resolved to real strings.
        for ((_, v) in step.params) {
            (v as? JsonPrimitive)?.contentOrNull?.let { parts += it.lowercase() }
        }
        return parts.distinct()
    }

    private suspend fun verify(step: PlannedStepOut, appPackage: String): StepOutcome {
        val node = waitForNode(step) ?: askResolverFor(step, appPackage)
        return if (node != null) StepOutcome.Success else StepOutcome.Failed("verify_node_missing")
    }

    // ─── Selector lookup ─────────────────────────────────────────────

    private suspend fun waitForNode(step: PlannedStepOut): AccessibilityNodeInfo? {
        val deadline = System.currentTimeMillis() + step.timeoutMs
        // Flat selector keys (text / contentDescription / viewId / className)
        // run through findNode's cascade. The sibling_anchor key is parsed
        // separately because its value is a nested JsonObject, not a string.
        val flatSelectors = step.targetSelector
            .filterKeys { it != "sibling_anchor" }
            .mapValues { (it.value as? JsonPrimitive)?.contentOrNull }
        val anchorObj = step.targetSelector["sibling_anchor"] as? kotlinx.serialization.json.JsonObject

        while (System.currentTimeMillis() < deadline) {
            val root = service.rootInActiveWindow
            // Primary cascade
            ScreenTreeReader.findNode(root, flatSelectors)?.let { return it }
            // Sibling-anchor fallback: only fires when the playbook step
            // explicitly opted in. Generated by the Opus improver for
            // chronically-stale selectors that have stable neighbours.
            if (anchorObj != null) {
                val nestedAnchor = (anchorObj["anchor"] as? kotlinx.serialization.json.JsonObject)
                    ?.mapValues { (it.value as? JsonPrimitive)?.contentOrNull }
                    ?: emptyMap()
                val direction = (anchorObj["direction"] as? JsonPrimitive)?.contentOrNull
                    ?: "next_clickable"
                if (nestedAnchor.isNotEmpty()) {
                    ScreenTreeReader.findBySiblingAnchor(root, nestedAnchor, direction)
                        ?.let { return it }
                }
            }
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
        val STOPWORDS = setOf(
            "tap", "type", "wait", "the", "on", "in", "to", "and", "for", "with",
            "from", "into", "this", "that", "app", "screen", "page", "if", "else",
            "first", "matching", "navigation", "bottom", "top", "icon",
        )

        // Confidence-floor knobs for the last-ditch keyword guesser. A wrong
        // tap here on a "Send" / "Delete" / "Pay" button is the worst-case
        // user experience; conservative defaults keep the false-tap rate low.
        const val MIN_DISTINCT_KEYWORDS = 2   // step desc must yield ≥2 specific words
        const val MIN_KEYWORD_HITS = 2        // winner must match ≥2 of those
        const val MIN_LEAD = 1                // winner must beat runner-up by ≥1
    }
}
