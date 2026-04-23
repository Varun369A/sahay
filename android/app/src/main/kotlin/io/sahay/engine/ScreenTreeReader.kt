package io.sahay.engine

import android.view.accessibility.AccessibilityNodeInfo

/**
 * Reads + searches the live accessibility tree.
 * All screen content stays here on-device — never leaves.
 */
object ScreenTreeReader {

    /**
     * Find a node matching ANY of the supplied selectors.
     * Selectors map keys: "viewId", "text", "contentDescription", "className".
     */
    fun findNode(root: AccessibilityNodeInfo?, selectors: Map<String, String?>): AccessibilityNodeInfo? {
        if (root == null) return null

        // Strategy 1: viewId — most reliable.
        selectors["viewId"]?.let { id ->
            val matches = root.findAccessibilityNodeInfosByViewId(id)
            if (!matches.isNullOrEmpty()) return matches.first()
        }

        // Strategy 2: text — exact match.
        selectors["text"]?.let { txt ->
            val matches = root.findAccessibilityNodeInfosByText(txt)
            val exact = matches?.firstOrNull { it.text?.toString()?.equals(txt, ignoreCase = true) == true }
            if (exact != null) return exact
            if (!matches.isNullOrEmpty()) return matches.first()
        }

        // Strategy 3: contentDescription — depth-first walk.
        selectors["contentDescription"]?.let { cd ->
            walk(root)?.let { it ->
                if (it.contentDescription?.toString()?.equals(cd, ignoreCase = true) == true) return it
            }
            return walkFind(root) {
                it.contentDescription?.toString()?.equals(cd, ignoreCase = true) == true
            }
        }

        // Strategy 4: className.
        selectors["className"]?.let { cn ->
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
            val cls = node.className?.toString() ?: ""
            val cd = node.contentDescription?.toString()?.take(40) ?: ""
            val hasText = (node.text?.toString()?.isNotEmpty() == true)
            sb.append("{\"vid\":\"").append(viewId).append('"')
            sb.append(",\"cls\":\"").append(cls).append('"')
            sb.append(",\"cd\":\"").append(cd.replace("\"", "")).append('"')
            sb.append(",\"hasText\":").append(hasText)
            sb.append(",\"clickable\":").append(node.isClickable)
            sb.append("},")
            count++
            true
        }
        if (sb.endsWith(",")) sb.setLength(sb.length - 1)
        sb.append("]}")
        return sb.toString()
    }

    // ─── Internals ────────────────────────────────────────────────────

    private fun walk(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var cur: AccessibilityNodeInfo? = root
        while (cur != null) return cur
        return null
    }

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
