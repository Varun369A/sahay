# Sahay ProGuard rules — minimal set; defaults from android-optimize cover most.

# Kotlinx serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keep,includedescriptorclasses class io.sahay.**$$serializer { *; }
-keepclassmembers class io.sahay.** {
    *** Companion;
}
-keepclasseswithmembers class io.sahay.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# Retrofit
-dontwarn org.codehaus.mojo.animal_sniffer.IgnoreJRERequirement
-dontwarn javax.annotation.**
-keepclasseswithmembers class * { @retrofit2.http.* <methods>; }
-keepattributes Signature, Exceptions

# OkHttp
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# SQLCipher
-keep class net.zetetic.database.** { *; }
-keep class net.sqlcipher.** { *; }

# Hilt-generated
-keep class * extends dagger.hilt.android.HiltAndroidApp

# Tink (via androidx.security.crypto) references optional Google libs
# that we don't ship (google-api-client, joda-time, ErrorProne annotations).
-dontwarn com.google.errorprone.annotations.**
-dontwarn com.google.api.client.**
-dontwarn org.joda.time.**
-keep class com.google.crypto.tink.** { *; }

# Vosk — loaded via JNI, ProGuard can't see the references.
-keep class org.kaldi.** { *; }
-keep class org.vosk.** { *; }

# reportlab / python-pptx (server-side only) — not needed here.

# Kotlin coroutines — keep continuation for stack traces.
-keepclassmembers class kotlinx.coroutines.internal.MainDispatcherFactory { *; }

# Accessibility + notification listener services — framework reflects
# into them via manifest action lookups; don't shrink them away.
-keep class io.sahay.service.SahayAccessibilityService { *; }
-keep class io.sahay.service.SahayForegroundService { *; }
-keep class io.sahay.service.WakeWordService { *; }
-keep class io.sahay.service.SahayNotificationListenerService { *; }
