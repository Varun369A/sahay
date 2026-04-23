package io.sahay.service

import android.accessibilityservice.AccessibilityService
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import io.sahay.engine.KillSwitch
import io.sahay.engine.PlaybookExecutor

/**
 * Sahay's "hands". Injects clicks + types into other apps via the
 * Accessibility framework. Owned by the OS; lifecycle is controlled by the
 * user toggling the service in system settings.
 *
 * Hard rules (defense in depth — server enforces too):
 *   • Refuses any action when foreground app is on PaymentBlocklist
 *   • Volume-down 3x within 2s = global kill switch (KillSwitch.engage)
 */
class SahayAccessibilityService : AccessibilityService() {

    @Volatile private var executor: PlaybookExecutor? = null
    private var lastVolDownTimes: MutableList<Long> = mutableListOf()

    override fun onServiceConnected() {
        super.onServiceConnected()
        executor = PlaybookExecutor(this)
        INSTANCE = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Foreground tracking — used by KillSwitch + payment guard.
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            val pkg = event.packageName?.toString()
            CurrentForeground.update(pkg)
        }
    }

    override fun onKeyEvent(event: KeyEvent?): Boolean {
        if (event?.action != KeyEvent.ACTION_DOWN) return false
        if (event.keyCode == KeyEvent.KEYCODE_VOLUME_DOWN) {
            val now = System.currentTimeMillis()
            lastVolDownTimes.add(now)
            // Drop entries older than 2 seconds.
            lastVolDownTimes.removeAll { now - it > 2000 }
            if (lastVolDownTimes.size >= 3) {
                lastVolDownTimes.clear()
                KillSwitch.engage(this)
                return true
            }
        }
        return false
    }

    override fun onInterrupt() {}

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        INSTANCE = null
        executor = null
        return super.onUnbind(intent)
    }

    fun executor(): PlaybookExecutor? = executor

    companion object {
        @Volatile private var INSTANCE: SahayAccessibilityService? = null
        fun instance(): SahayAccessibilityService? = INSTANCE
    }
}

/** Globally readable last-known foreground package. */
object CurrentForeground {
    @Volatile var packageName: String? = null
        private set
    fun update(pkg: String?) { packageName = pkg }
}
