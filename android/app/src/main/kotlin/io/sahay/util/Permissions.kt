package io.sahay.util

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.core.content.ContextCompat

object Permissions {

    fun hasMic(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    fun hasNotifications(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED

    /**
     * True iff Sahay's AccessibilityService is enabled in system settings.
     *
     * Reads `Settings.Secure.enabled_accessibility_services` directly
     * (same approach as [isNotificationListenerEnabled]) rather than
     * AccessibilityManager.getEnabledAccessibilityServiceList(feedbackType)
     * which filters by feedback-type bitmask and misses our service on
     * several OEM ROMs (Realme, Xiaomi, Vivo). The raw setting is a
     * colon-separated list of `packageName/serviceFqcn` entries — we just
     * substring-match our package.
     *
     * `serviceFqcn` is kept for backward compatibility but optional; a
     * simple package-name match is sufficient and more resilient to
     * build-variant suffixes like `.debug`.
     */
    fun isAccessibilityEnabled(context: Context, serviceFqcn: String): Boolean {
        val flat = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ) ?: return false
        val pkg = context.packageName
        return flat.split(':').any { entry ->
            entry.contains(pkg, ignoreCase = true) &&
                // When an explicit FQCN was passed, honour it; otherwise
                // package-only is fine. Allows the check to survive class
                // renames and debug suffixes.
                (serviceFqcn.isBlank() || entry.contains(serviceFqcn, ignoreCase = true) ||
                    entry.contains(serviceFqcn.substringAfterLast('.'), ignoreCase = true))
        }
    }

    fun allGranted(context: Context, accessibilityServiceFqcn: String): Boolean =
        hasMic(context) && hasNotifications(context) &&
            isAccessibilityEnabled(context, accessibilityServiceFqcn)

    fun openAccessibilitySettings(context: Context) {
        val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }

    fun openAppSettings(context: Context) {
        val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = android.net.Uri.fromParts("package", context.packageName, null)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }

    /** True if Sahay's NotificationListenerService is enabled in system settings. */
    fun isNotificationListenerEnabled(context: Context): Boolean {
        // enabled_notification_listeners is a colon-separated list of
        // ComponentName strings. Cheaper + more reliable than reflecting into
        // NotificationManagerCompat across OEM ROMs.
        val flat = Settings.Secure.getString(
            context.contentResolver,
            "enabled_notification_listeners",
        ) ?: return false
        val pkg = context.packageName
        return flat.split(':').any { it.contains(pkg, ignoreCase = true) }
    }

    fun openNotificationListenerSettings(context: Context) {
        val intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }
}
