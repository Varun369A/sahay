package io.sahay.engine

import android.view.accessibility.AccessibilityNodeInfo

/**
 * Reads + searches the live accessibility tree.
 * All screen content stays here on-device — never leaves.
 */
object ScreenTreeReader {

    /**
     * Find a node matching ANY of the supplied selectors.
     * Strategy is tolerant: tries exact match first, then case-insensitive
     * substring match for text + contentDescription. Falls back to a wide
     * tree walk for className.
     *
     * Selector keys: "viewId", "text", "contentDescription", "className".
     */
    fun findNode(root: AccessibilityNodeInfo?, selectors: Map<String, String?>): AccessibilityNodeInfo? {
        if (root == null) return null

        // Strategy 1: viewId — most reliable.
        selectors["viewId"]?.takeIf { it.isNotBlank() }?.let { id ->
            val matches = root.findAccessibilityNodeInfosByViewId(id)
            if (!matches.isNullOrEmpty()) return matches.first()
        }

        // Strategy 2: exact text.
        selectors["text"]?.takeIf { it.isNotBlank() }?.let { txt ->
            val exact = walkFind(root) { it.text?.toString().equals(txt, ignoreCase = true) }
            if (exact != null) return exact
            // Fuzzy: contains
            val fuzzy = walkFind(root) {
                it.text?.toString()?.contains(txt, ignoreCase = true) == true
            }
            if (fuzzy != null) return fuzzy
        }

        // Strategy 3: contentDescription.
        selectors["contentDescription"]?.takeIf { it.isNotBlank() }?.let { cd ->
            val exact = walkFind(root) {
                it.contentDescription?.toString().equals(cd, ignoreCase = true)
            }
            if (exact != null) return exact
            val fuzzy = walkFind(root) {
                it.contentDescription?.toString()?.contains(cd, ignoreCase = true) == true
            }
            if (fuzzy != null) return fuzzy
        }

        // Strategy 4: className.
        selectors["className"]?.takeIf { it.isNotBlank() }?.let { cn ->
            return walkFind(root) { it.className?.toString() == cn }
        }

        return null
    }

    /** Compress the tree into a short text summary for the cloud planner (no content fields). */
    fun summarize(root: AccessibilityNodeInfo?, maxNodes: Int = 200): String {
        if (root == null) return "{}"
        val sb = StringBuilder()
        sb.append("{\"package\":\"").append(root.packageName ?: "").append("\",\"nodes\":[")
        var count = 0
        walkAll(root) { node ->
            if (count >= maxNodes) return@walkAll false
            val viewId = node.viewIdResourceName ?: ""
            val cls = node.className?.toString()?.substringAfterLast('.') ?: ""
            val cd = node.contentDescription?.toString()?.take(60)?.replace("\"", "'") ?: ""
            val text = node.text?.toString()?.take(60)?.replace("\"", "'") ?: ""
            // Skip empty noise nodes — keeps token count low.
            if (viewId.isBlank() && cd.isBlank() && text.isBlank() && !node.isClickable) {
                count++
                return@walkAll true
            }
            sb.append("{\"vid\":\"").append(viewId).append('"')
            sb.append(",\"cls\":\"").append(cls).append('"')
            if (cd.isNotEmpty()) sb.append(",\"cd\":\"").append(cd).append('"')
            if (text.isNotEmpty()) sb.append(",\"text\":\"").append(text).append('"')
            if (node.isClickable) sb.append(",\"clickable\":true")
            if (node.isEditable) sb.append(",\"editable\":true")
            sb.append("},")
            count++
            true
        }
        if (sb.endsWith(",")) sb.setLength(sb.length - 1)
        sb.append("]}")
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
            for (i in 0 until n.childCount) {
                n.getChild(i)?.let(stack::addLast)
            }
        }
        return null
    }

    private inline fun walkAll(
        root: AccessibilityNodeInfo,
        crossinline visit: (AccessibilityNodeInfo) -> Boolean,
    ) {
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (!visit(n)) return
            for (i in 0 until n.childCount) {
                n.getChild(i)?.let(stack::addLast)
            }
        }
    }
}
