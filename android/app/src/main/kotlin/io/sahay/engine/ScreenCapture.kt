package io.sahay.engine

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import android.util.Base64
import android.util.Log
import android.view.Display
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Captures the current phone screen via AccessibilityService.takeScreenshot()
 * (Android 11 / API 30+). No extra permission required — we already have
 * accessibility access.
 *
 * Returns the PNG as base64, ready to embed in a Claude vision request.
 * Downsamples to max 1080px wide to keep payload + tokens manageable
 * (Claude Haiku vision pricing benefits from smaller images).
 */
object ScreenCapture {

    private const val TAG = "ScreenCapture"
    private const val MAX_WIDTH = 1080
    private const val PNG_QUALITY = 85

    /**
     * Grab the display. Returns null if API < 30, no display, or capture failed.
     *
     * @return (base64Png, widthPx, heightPx) or null
     */
    suspend fun capture(service: AccessibilityService): Capture? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            Log.w(TAG, "screenshot_requires_api_30")
            return null
        }
        val bitmap = withTimeoutOrNull(3_000) { takeScreenshotSuspending(service) } ?: run {
            Log.w(TAG, "screenshot_timeout_or_null")
            return null
        }
        return withContext(Dispatchers.Default) { encode(bitmap) }
    }

    data class Capture(val base64Png: String, val width: Int, val height: Int)

    // ─── Internals ────────────────────────────────────────────────────

    private suspend fun takeScreenshotSuspending(
        service: AccessibilityService,
    ): Bitmap? = suspendCancellableCoroutine { cont ->
        try {
            @Suppress("NewApi")
            service.takeScreenshot(
                Display.DEFAULT_DISPLAY,
                service.mainExecutor,
                object : AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(screenshot: AccessibilityService.ScreenshotResult) {
                        try {
                            val buffer = screenshot.hardwareBuffer
                            val bitmap = Bitmap.wrapHardwareBuffer(buffer, screenshot.colorSpace)
                            buffer.close()
                            // Copy to software bitmap so we can encode it.
                            val sw = bitmap?.copy(Bitmap.Config.ARGB_8888, false)
                            bitmap?.recycle()
                            if (cont.isActive) cont.resume(sw)
                        } catch (t: Throwable) {
                            Log.e(TAG, "bitmap_conversion_failed", t)
                            if (cont.isActive) cont.resume(null)
                        }
                    }

                    override fun onFailure(errorCode: Int) {
                        Log.w(TAG, "takeScreenshot_failed code=$errorCode")
                        if (cont.isActive) cont.resume(null)
                    }
                },
            )
        } catch (t: Throwable) {
            Log.e(TAG, "takeScreenshot_throw", t)
            if (cont.isActive) cont.resume(null)
        }
    }

    private fun encode(bitmap: Bitmap): Capture {
        // Downsample if wider than MAX_WIDTH.
        val scaled = if (bitmap.width > MAX_WIDTH) {
            val scale = MAX_WIDTH.toFloat() / bitmap.width
            val newH = (bitmap.height * scale).toInt()
            Bitmap.createScaledBitmap(bitmap, MAX_WIDTH, newH, true).also {
                if (it !== bitmap) bitmap.recycle()
            }
        } else bitmap

        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.PNG, PNG_QUALITY, out)
        val b64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        val w = scaled.width
        val h = scaled.height
        scaled.recycle()
        Log.i(TAG, "captured ${w}x${h} bytes=${b64.length}")
        return Capture(b64, w, h)
    }
}
