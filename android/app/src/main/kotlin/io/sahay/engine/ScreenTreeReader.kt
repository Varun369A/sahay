package io.sahay.engine

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Reads + searches the live accessibility tree.
 * All screen content stays here on-device — never leaves.
 *
 * Enhanced for agent-mode: each interactive node is indexed and its
 * on-screen bounds (center point) are recorded, so the backend agent
 * can reference nodes as `node 7` and we compute the exact tap target.
 */
object ScreenTreeReader {

    /** Stable in-memory index of interactive nodes from the most recent summary call. */
    @Volatile
    private var lastIndexedNodes: List<IndexedNode> = emptyList()

    /**
     * Side-channel telemetry: which selector tier won the most recent
     * findNode/findBySiblingAnchor call. Read by callers that want to
     * attribute the match (PlaybookExecutor for the selector_trainer
     * pipeline). Reset to null on a miss.
     *
     * Tier names — keep stable; they're persisted as JSON keys server-side:
     *   "viewId", "text_exact", "cd_exact",
     *   "text_substring", "cd_substring", "className",
     *   "sibling_anchor"
     */
    @Volatile
    var lastWinningTier: String? = null
        private set

    data class IndexedNode(
        val index: Int,
        val node: AccessibilityNodeInfo,
        val centerX: Int,
        val centerY: Int,
        val viewId: String,
        val text: String,
        val contentDescription: String,
        val clickable: Boolean,
        val editable: Boolean,
    )

    /** Resolve a previously-indexed node by its number. Only valid until next summarize(). */
    fun nodeForIndex(i: Int): IndexedNode? =
        lastIndexedNodes.firstOrNull { it.index == i }

    /**
     * Find a node matching any of the supplied selectors.
     *
     * Try order — every step is a fallback for the previous one. Apps drift
     * fast (rename buttons, change viewIds on rebuild, ship i18n updates),
     * so we cascade through every signal we have before giving up:
     *
     *   1. viewId        — most stable when it works (the app explicitly
     *                      named the view) but easily missed across builds
     *   2. text exact    — what the user reads. Brittle to i18n + a/b tests
     *   3. contentDescription exact — i18n-resilient when the app sets it
     *   4. text substring         — handles "Send message" matching "Send"
     *   5. contentDescription substring
     *   6. className              — last resort, very loose
     *   7. sibling_anchor         — "the first clickable node after the
     *                                node with text='Type a message'".
     *                              The patch the nightly Opus improver
     *                              generates for `selector_stale` failures.
     *
     * Critical: each step that misses MUST fall through to the next one.
     * Earlier versions returned null after a missed substring search even
     * when a later selector key was provided — that masked the whole
     * cascade and made playbooks fragile in the wild.
     *
     * Selector keys recognised:
     *   "viewId", "text", "contentDescription", "className",
     *   "sibling_anchor" (parsed as JSON in `findNodeByJson`).
     */
    fun findNode(
        root: AccessibilityNodeInfo?,
        selectors: Map<String, String?>,
    ): AccessibilityNodeInfo? {
        lastWinningTier = null
        if (root == null) return null

        // 1. viewId — exact, structured ID match
        selectors["viewId"]?.takeIf { it.isNotBlank() && !it.all { c -> c.isDigit() } }?.let { id ->
            val matches = root.findAccessibilityNodeInfosByViewId(id)
            if (!matches.isNullOrEmpty()) {
                lastWinningTier = "viewId"
                return matches.first()
            }
        }

        // 2. text exact (case-insensitive)
        selectors["text"]?.takeIf { it.isNotBlank() }?.let { txt ->
            walkFind(root) { it.text?.toString().equals(txt, ignoreCase = true) }
                ?.let { lastWinningTier = "text_exact"; return it }
        }

        // 3. contentDescription exact
        selectors["contentDescription"]?.takeIf { it.isNotBlank() }?.let { cd ->
            walkFind(root) {
                it.contentDescription?.toString().equals(cd, ignoreCase = true)
            }?.let { lastWinningTier = "cd_exact"; return it }
        }

        // 4. text substring — covers "Send message" matching the literal "Send"
        selectors["text"]?.takeIf { it.isNotBlank() }?.let { txt ->
            walkFind(root) {
                it.text?.toString()?.contains(txt, ignoreCase = true) == true
            }?.let { lastWinningTier = "text_substring"; return it }
        }

        // 5. contentDescription substring
        selectors["contentDescription"]?.takeIf { it.isNotBlank() }?.let { cd ->
            walkFind(root) {
                it.contentDescription?.toString()?.contains(cd, ignoreCase = true) == true
            }?.let { lastWinningTier = "cd_substring"; return it }
        }

        // 6. className — very loose; only fires when the playbook really
        // means "any EditText on the screen"
        selectors["className"]?.takeIf { it.isNotBlank() }?.let { cn ->
            walkFind(root) { it.className?.toString() == cn }?.let {
                lastWinningTier = "className"; return it
            }
        }

        return null
    }

    /**
     * Sibling-anchor fallback. Resolves selectors of the form:
     *
     *   {"sibling_anchor": {
     *     "anchor": {"text": "Type a message"},
     *     "direction": "next_clickable"     // or "prev_clickable"
     *   }}
     *
     * "Find the anchor (using the same cascade as findNode), then walk
     * forward in the tree to the next clickable node." This is the
     * structural fallback the Opus improver generates when a label-based
     * selector keeps going stale across app updates — the relative
     * position to a stable label often survives renames.
     *
     * Returns null if either the anchor or the target can't be resolved.
     */
    fun findBySiblingAnchor(
        root: AccessibilityNodeInfo?,
        anchorSelectors: Map<String, String?>,
        direction: String,
    ): AccessibilityNodeInfo? {
        if (root == null) return null
        val anchor = findNode(root, anchorSelectors) ?: return null

        // Flatten the tree into pre-order traversal so "next" / "prev" have
        // a stable definition. Filter to clickable nodes only — this method
        // exists specifically to find a clickable target.
        val clickables = mutableListOf<AccessibilityNodeInfo>()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (n.isClickable) clickables += n
            for (i in n.childCount - 1 downTo 0) {       // reverse so pop pulls in document order
                n.getChild(i)?.let(stack::addLast)
            }
        }
        val anchorIdx = clickables.indexOfFirst { it == anchor }
        val picked = when (direction) {
            "next_clickable" -> {
                if (anchorIdx < 0) clickables.firstOrNull()
                else clickables.getOrNull(anchorIdx + 1)
            }
            "prev_clickable" -> {
                if (anchorIdx < 0) null
                else clickables.getOrNull(anchorIdx - 1)
            }
            else -> null
        }
        if (picked != null) lastWinningTier = "sibling_anchor"
        return picked
    }

    /**
     * Compact JSON summary of the tree with STABLE INDEX for every interactive
     * node. Format is small and LLM-friendly — each node has an `i` (index the
     * agent refers to), position, and labels.
     *
     * Example:
     * {"package":"com.netflix...","w":720,"h":1600,"nodes":[
     *   {"i":0,"x":654,"y":120,"cls":"Button","cd":"Search","clk":true},
     *   {"i":1,"x":360,"y":240,"cls":"EditText","hint":"Search","edit":true},
     *   {"i":2,"x":180,"y":800,"cls":"TextView","text":"Dune","clk":true}
     * ]}
     */
    fun summarize(root: AccessibilityNodeInfo?, maxNodes: Int = 120): String {
        if (root == null) {
            lastIndexedNodes = emptyList()
            return "{}"
        }
        val indexed = mutableListOf<IndexedNode>()
        val sb = StringBuilder()
        sb.append("{\"package\":\"").append(root.packageName ?: "").append('"')

        val screenRect = Rect()
        root.getBoundsInScreen(screenRect)
        sb.append(",\"w\":").append(screenRect.width())
        sb.append(",\"h\":").append(screenRect.height())
        sb.append(",\"nodes\":[")

        var idx = 0
        val bounds = Rect()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)

        while (stack.isNotEmpty() && idx < maxNodes) {
            val n = stack.removeLast()
            val clickable = n.isClickable
            val editable = n.isEditable
            val hasLabel = !n.text.isNullOrBlank() || !n.contentDescription.isNullOrBlank()

            // Skip pure layout containers to reduce noise — but keep anything
            // that can be acted on (clickable or editable) OR has user-visible text.
            if (clickable || editable || hasLabel) {
                n.getBoundsInScreen(bounds)
                val cx = bounds.centerX()
                val cy = bounds.centerY()
                // Skip off-screen / zero-size nodes
                if (bounds.width() > 8 && bounds.height() > 8 && cx >= 0 && cy >= 0) {
                    val viewId = n.viewIdResourceName ?: ""
                    val text = n.text?.toString()?.take(80)?.replace("\"", "'") ?: ""
                    val cd = n.contentDescription?.toString()?.take(80)?.replace("\"", "'") ?: ""
                    val cls = n.className?.toString()?.substringAfterLast('.') ?: ""

                    if (idx > 0) sb.append(',')
                    sb.append("{\"i\":").append(idx)
                    sb.append(",\"x\":").append(cx)
                    sb.append(",\"y\":").append(cy)
                    sb.append(",\"cls\":\"").append(cls).append('"')
                    if (text.isNotEmpty())   sb.append(",\"text\":\"").append(text).append('"')
                    if (cd.isNotEmpty())     sb.append(",\"cd\":\"").append(cd).append('"')
                    if (viewId.isNotEmpty()) sb.append(",\"vid\":\"").append(
                        viewId.substringAfterLast('/')
                    ).append('"')
                    if (clickable)           sb.append(",\"clk\":true")
                    if (editable)            sb.append(",\"edit\":true")
                    sb.append('}')

                    indexed += IndexedNode(idx, n, cx, cy, viewId, text, cd, clickable, editable)
                    idx++
                }
            }
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }
        sb.append("]}")

        lastIndexedNodes = indexed
        return sb.toString()
    }

    // ─── Internals ────────────────────────────────────────────────────

    private inline fun walkFind(
        root: AccessibilityNodeInfo,
        crossinline predicate: (AccessibilityNodeInfo) -> Boolean,
    ): AccessibilityNodeInfo? {
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (predicate(n)) return n
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }
        return null
    }
}
