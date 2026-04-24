package io.sahay.engine

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.util.Log
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Pixel-level touch dispatcher backed by AccessibilityService.dispatchGesture().
 * Used by AgentLoop when Claude returns tap/long_press/swipe with (x, y) coords.
 *
 * Coords are in screen pixels, origin (0, 0) = top-left.
 * dispatchGesture returns true once the gesture is accepted (not completed);
 * we suspend until the onCompleted/onCancelled callback fires.
 */
object GestureDispatcher {

    private const val TAG = "Gesture"

    suspend fun tap(service: AccessibilityService, x: Int, y: Int): Boolean =
        dispatchPath(service, buildTapPath(x, y), startTime = 0, duration = 80)

    suspend fun longPress(service: AccessibilityService, x: Int, y: Int): Boolean =
        dispatchPath(service, buildTapPath(x, y), startTime = 0, duration = 600)

    suspend fun swipe(
        service: AccessibilityService,
        fromX: Int,
        fromY: Int,
        toX: Int,
        toY: Int,
        durationMs: Long = 300,
    ): Boolean {
        val path = Path().apply {
            moveTo(fromX.toFloat(), fromY.toFloat())
            lineTo(toX.toFloat(), toY.toFloat())
        }
        return dispatchPath(service, path, startTime = 0, duration = durationMs)
    }

    // ─── Internals ────────────────────────────────────────────────────

    private fun buildTapPath(x: Int, y: Int): Path = Path().apply {
        moveTo(x.toFloat(), y.toFloat())
    }

    private suspend fun dispatchPath(
        service: AccessibilityService,
        path: Path,
        startTime: Long,
        duration: Long,
    ): Boolean {
        val stroke = GestureDescription.StrokeDescription(path, startTime, duration)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()

        val result = withTimeoutOrNull(duration + 1500) {
            suspendCancellableCoroutine<Boolean> { cont ->
                val callback = object : AccessibilityService.GestureResultCallback() {
                    override fun onCompleted(description: GestureDescription?) {
                        if (cont.isActive) cont.resume(true)
                    }
                    override fun onCancelled(description: GestureDescription?) {
                        if (cont.isActive) cont.resume(false)
                    }
                }
                val accepted = service.dispatchGesture(gesture, callback, null)
                if (!accepted) {
                    Log.w(TAG, "dispatchGesture_rejected")
                    if (cont.isActive) cont.resume(false)
                }
            }
        }
        return result ?: false
    }
}
