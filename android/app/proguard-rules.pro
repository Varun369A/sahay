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
