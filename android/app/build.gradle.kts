import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.hilt)
    alias(libs.plugins.ksp)
}

android {
    namespace = "io.sahay"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.sahay"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables { useSupportLibrary = true }

        // BuildConfig fields — read from local.properties first, then -P, then default.
        //
        // Set in android/local.properties:
        //     SAHAY_API_BASE=https://sahay-production-5dbf.up.railway.app
        //     SAHAY_SARVAM_KEY=sk_xxx
        val localProps = Properties().apply {
            val f = rootProject.file("local.properties")
            if (f.exists()) f.inputStream().use { stream -> load(stream) }
        }
        fun prop(name: String, default: String): String =
            (project.findProperty(name) as String?)
                ?: localProps.getProperty(name)
                ?: default

        val apiBase = prop("SAHAY_API_BASE", "http://10.0.2.2:8080")
        val sarvamKey = prop("SAHAY_SARVAM_KEY", "")
        buildConfigField("String", "API_BASE_URL", "\"$apiBase\"")
        buildConfigField("String", "SARVAM_API_KEY", "\"$sarvamKey\"")
        println("Sahay build → API_BASE_URL=$apiBase")
    }

    signingConfigs {
        create("release") {
            // Configured at APK build time. See android/scripts/build_release.md
            val storePath = project.findProperty("SAHAY_KEYSTORE") as String?
            if (storePath != null) {
                storeFile = file(storePath)
                storePassword = project.findProperty("SAHAY_KEYSTORE_PASSWORD") as String?
                keyAlias = project.findProperty("SAHAY_KEY_ALIAS") as String?
                keyPassword = project.findProperty("SAHAY_KEY_PASSWORD") as String?
            }
        }
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "META-INF/DEPENDENCIES", "META-INF/LICENSE*")
        }
    }

    sourceSets["main"].kotlin.srcDirs("src/main/kotlin")
    sourceSets["test"].kotlin.srcDirs("src/test/kotlin")
    sourceSets["androidTest"].kotlin.srcDirs("src/androidTest/kotlin")
}

dependencies {
    // Compose
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons.extended)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.core.ktx)
    implementation(libs.nav.compose)

    // DI
    implementation(libs.hilt.android)
    implementation(libs.hilt.nav.compose)
    ksp(libs.hilt.compiler)

    // Network
    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    // Storage
    implementation(libs.room.runtime)
    implementation(libs.room.ktx)
    ksp(libs.room.compiler)
    implementation(libs.sqlcipher)
    implementation(libs.sqlite)
    implementation(libs.datastore.preferences)
    implementation(libs.security.crypto)

    // Background
    implementation(libs.work.runtime)

    // UX
    implementation(libs.splashscreen)

    // Voice — wake word handled by Android's built-in SpeechRecognizer
    // (zero extra deps, free, on-device on most modern Androids)

    // Debug
    debugImplementation(libs.compose.ui.tooling)

    // Test
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.espresso.core)
}
