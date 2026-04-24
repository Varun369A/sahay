package io.sahay.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import io.sahay.engine.ScreenTreeReader
import java.io.File

/**
 * Dev-only: dump the live accessibility tree on demand via ADB broadcast.
 * Bypasses uiautomator's "could not get idle state" failure on animated screens.
 *
 * Usage:
 *   adb shell am broadcast -a io.sahay.debug.DUMP_TREE
 *   adb pull /sdcard/Android/data/io.sahay.debug/files/tree.json
 *
 * Registered dynamically only in debug builds.
 */
class DebugDumpReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_DUMP) return
        val service = SahayAccessibilityService.instance() ?: run {
            Log.e(TAG, "accessibility_service_not_running")
            return
        }
        val tree = ScreenTreeReader.summarize(service.rootInActiveWindow, maxNodes = 500)
        val pkg = service.rootInActiveWindow?.packageName?.toString() ?: "unknown"
        val out = File(context.getExternalFilesDir(null), "tree.json")
        out.writeText(tree)
        Log.i(TAG, "dumped pkg=$pkg bytes=${out.length()} → ${out.absolutePath}")
    }

    companion object {
        const val ACTION_DUMP = "io.sahay.debug.DUMP_TREE"
        private const val TAG = "DebugDump"
    }
}
