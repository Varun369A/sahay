package io.sahay.engine

import android.accessibilityservice.AccessibilityService
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Polls a step's `expect` post-condition after the action runs.
 *
 * Why this exists: a successful AccessibilityService gesture call only
 * means "the gesture was dispatched", NOT "the right thing happened on
 * screen". A tap on a stale node, a typed string the IME swallowed, or a
 * dialog that intercepted the tap all return success at the API layer.
 * The verifier is what turns "the action ran" into "the action did the
 * right thing".
 *
 * Usage from `PlaybookExecutor`:
 *
 *   val before = StepVerifier.snapshotHash(service)   // pre-action
 *   val outcome = doTheAction(...)
 *   if (outcome is Success && step.expect != null) {
 *     val v = StepVerifier.verify(service, step.expect, beforeHash = before)
 *     if (!v.passed) return StepOutcome.Failed("expect_failed:${v.reason}")
 *   }
 */
object StepVerifier {

    private const val TAG = "StepVerifier"
    private const val POLL_MS = 150L

    data class Result(val passed: Boolean, val reason: String)

    /**
     * Walk the live tree and check the expectation. Polls until satisfied
     * or `timeoutMs` elapses. `beforeHash` is required for `screen_changed`
     * — it should be the value of `snapshotHash` captured BEFORE the
     * action was dispatched.
     */
    suspend fun verify(
        service: AccessibilityService,
        expect: Expectation,
        beforeHash: Int? = null,
    ): Result {
        val kind = ExpectKind.fromWire(expect.kind)
            ?: return Result(false, "unknown_kind:${expect.kind}")

        val deadline = System.currentTimeMillis() + expect.timeoutMs
        while (System.currentTimeMillis() < deadline) {
            val root = service.rootInActiveWindow
            val passed = when (kind) {
                ExpectKind.SCREEN_CONTAINS  -> screenContains(root, expect.text)
                ExpectKind.NODE_APPEARS     -> findBySelector(root, expect.selector) != null
                ExpectKind.NODE_DISAPPEARS  -> findBySelector(root, expect.selector) == null
                ExpectKind.SCREEN_CHANGED   -> beforeHash != null && snapshotHash(root) != beforeHash
                ExpectKind.FIELD_VALUE      -> fieldHasValue(root, expect.selector, expect.text)
            }
            if (passed) {
                Log.i(TAG, "expect_pass kind=${expect.kind}")
                return Result(true, "ok")
            }
            delay(POLL_MS)
        }
        Log.w(TAG, "expect_fail kind=${expect.kind} timeout_ms=${expect.timeoutMs}")
        return Result(false, expect.kind)
    }

    /**
     * Cheap stable hash of the screen state. Sums identity-influencing
     * fields (className + text + contentDescription) for every node.
     * Used by `screen_changed` to detect "anything moved".
     */
    fun snapshotHash(root: AccessibilityNodeInfo?): Int {
        if (root == null) return 0
        val sb = StringBuilder(1024)
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            sb.append(n.className ?: "")
            sb.append('|')
            sb.append(n.text ?: "")
            sb.append('|')
            sb.append(n.contentDescription ?: "")
            sb.append(';')
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }
        return sb.toString().hashCode()
    }

    // ── Predicates ────────────────────────────────────────────────────

    private fun screenContains(root: AccessibilityNodeInfo?, needle: String?): Boolean {
        if (root == null || needle.isNullOrBlank()) return false
        val target = needle.trim()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (n.text?.toString()?.contains(target, ignoreCase = true) == true) return true
            if (n.contentDescription?.toString()?.contains(target, ignoreCase = true) == true) return true
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }
        return false
    }

    private fun findBySelector(
        root: AccessibilityNodeInfo?,
        selector: JsonObject?,
    ): AccessibilityNodeInfo? {
        if (root == null || selector == null) return null
        val flat = selector.mapValues { (_, v) ->
            (v as? JsonPrimitive)?.contentOrNull
        }
        return ScreenTreeReader.findNode(root, flat)
    }

    private fun fieldHasValue(
        root: AccessibilityNodeInfo?,
        selector: JsonObject?,
        expected: String?,
    ): Boolean {
        if (expected.isNullOrBlank()) return false
        val node = findBySelector(root, selector) ?: return false
        return node.text?.toString()?.trim() == expected.trim()
    }
}
