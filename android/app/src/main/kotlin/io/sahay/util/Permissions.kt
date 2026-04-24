package io.sahay.util

import android.Manifest
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.core.content.ContextCompat

object Permissions {

    fun hasMic(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    fun hasNotifications(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED

    /** True if Sahay's AccessibilityService is enabled in system settings. */
    fun isAccessibilityEnabled(context: Context, serviceFqcn: String): Boolean {
        val am = context.getSystemService(AccessibilityManager::class.java) ?: return false
        val enabled = am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_GENERIC)
        return enabled.any { it.id?.contains(serviceFqcn, ignoreCase = true) == true }
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
