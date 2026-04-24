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
     * Strategy is tolerant: exact match first, then case-insensitive substring.
     *
     * Selector keys: "viewId", "text", "contentDescription", "className".
     */
    fun findNode(
        root: AccessibilityNodeInfo?,
        selectors: Map<String, String?>,
    ): AccessibilityNodeInfo? {
        if (root == null) return null

        selectors["viewId"]?.takeIf { it.isNotBlank() && !it.all { c -> c.isDigit() } }?.let { id ->
            val matches = root.findAccessibilityNodeInfosByViewId(id)
            if (!matches.isNullOrEmpty()) return matches.first()
        }
        selectors["text"]?.takeIf { it.isNotBlank() }?.let { txt ->
            val exact = walkFind(root) { it.text?.toString().equals(txt, ignoreCase = true) }
            if (exact != null) return exact
            return walkFind(root) {
                it.text?.toString()?.contains(txt, ignoreCase = true) == true
            }
        }
        selectors["contentDescription"]?.takeIf { it.isNotBlank() }?.let { cd ->
            val exact = walkFind(root) {
                it.contentDescription?.toString().equals(cd, ignoreCase = true)
            }
            if (exact != null) return exact
            return walkFind(root) {
                it.contentDescription?.toString()?.contains(cd, ignoreCase = true) == true
            }
        }
        selectors["className"]?.takeIf { it.isNotBlank() }?.let { cn ->
            return walkFind(root) { it.className?.toString() == cn }
        }
        return null
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
