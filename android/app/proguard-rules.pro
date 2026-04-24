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

# Vosk — loaded via JNA; native code looks up specific field IDs
# (e.g. com.sun.jna.Pointer#peer). R8 mustn't rename OR remove any
# JNA/Vosk member or the recognizer dies at Model() init with
# UnsatisfiedLinkError: Can't obtain peer field ID for class com.sun.jna.Pointer.
-keep class org.kaldi.** { *; }
-keep class org.vosk.** { *; }
-keep class com.sun.jna.** { *; }
-keep class * extends com.sun.jna.** { *; }
-keepclassmembers class * extends com.sun.jna.** { *; }
-keepclasseswithmembers class * {
    native <methods>;
}
# JNA drags in java.awt references for desktop JVMs — never reached
# on Android, but R8 errors on them without a dontwarn.
-dontwarn java.awt.**
-dontwarn com.sun.jna.Native$AWT

# reportlab / python-pptx (server-side only) — not needed here.

# Kotlin coroutines — keep continuation for stack traces.
-keepclassmembers class kotlinx.coroutines.internal.MainDispatcherFactory { *; }

# Accessibility + notification listener services — framework reflects
# into them via manifest action lookups; don't shrink them away.
-keep class io.sahay.service.SahayAccessibilityService { *; }
-keep class io.sahay.service.SahayForegroundService { *; }
-keep class io.sahay.service.WakeWordService { *; }
-keep class io.sahay.service.SahayNotificationListenerService { *; }
