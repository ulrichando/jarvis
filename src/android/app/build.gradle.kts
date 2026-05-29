import com.google.protobuf.gradle.id
import java.util.Properties

// Personal-use secrets baked into the APK at build time. Read from local.properties
// (gitignored) so tokens survive an `adb uninstall` without ever hitting git history.
// Blank if the key is absent — production-safe if local.properties is not shipped.
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.let { f ->
        f.inputStream().use { load(it) }
    }
}
val defaultHfToken: String = localProps.getProperty("hf.token", "")

// Release signing credentials from local.properties (gitignored).
// Add these four keys to local.properties to enable a signed release APK:
//   release.keystore      = /absolute/path/to/your.keystore
//   release.key.alias     = <key alias>
//   release.store.password = <keystore password>
//   release.key.password  = <key password>
val releaseKeystorePath: String?    = localProps.getProperty("release.keystore")
val releaseKeyAlias: String?        = localProps.getProperty("release.key.alias")
val releaseStorePassword: String?   = localProps.getProperty("release.store.password")
val releaseKeyPassword: String?     = localProps.getProperty("release.key.password")
val hasReleaseSigningConfig: Boolean =
    releaseKeystorePath != null && releaseKeyAlias != null &&
    releaseStorePassword != null && releaseKeyPassword != null

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.kotlin.parcelize)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
    alias(libs.plugins.protobuf)
    alias(libs.plugins.android.junit5)
}

android {
    namespace   = "com.jarvis.android"
    compileSdk  = 35

    ndkVersion  = "27.2.12479018"  // LTS NDK — stable for openpty / POSIX PTY

    defaultConfig {
        applicationId   = "com.jarvis.android"
        minSdk          = 26
        targetSdk       = 35
        versionCode     = 1
        versionName     = "1.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // ── NDK / CMake ───────────────────────────────────────────────────
        externalNativeBuild {
            cmake {
                // xterm-256color + openpty require _XOPEN_SOURCE; enable C++17
                cppFlags("-std=c++17", "-D_XOPEN_SOURCE=700", "-DANDROID_STL=c++_shared")
                arguments(
                    "-DANDROID_PLATFORM=android-26",
                    "-DANDROID_STL=c++_shared",
                    "-DCMAKE_BUILD_TYPE=Release",
                )
            }
        }
        ndk {
            // Ship arm64 (modern phones) + x86_64 (emulator / dev).
            // Drop armeabi-v7a — root tools on 32-bit are rare and inflate APK size.
            abiFilters += setOf("arm64-v8a", "x86_64")
        }

        // ── Room: export schema for migration tracking ────────────────────
        ksp {
            arg("room.schemaLocation", "$projectDir/schemas")
            arg("room.incremental",    "true")
            arg("room.expandProjection", "true")
        }

        // ── BuildConfig fields ────────────────────────────────────────────
        buildConfigField("String", "ANTHROPIC_API_BASE", "\"https://api.anthropic.com\"")
        buildConfigField("String", "ANTHROPIC_VERSION",  "\"2023-06-01\"")
        buildConfigField("String", "DEFAULT_HF_TOKEN",   "\"$defaultHfToken\"")
    }

    // ── NDK external build entry point ────────────────────────────────────
    externalNativeBuild {
        cmake {
            path    = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    // ── Signing configs ───────────────────────────────────────────────────
    // Only wired when all four release.* keys are present in local.properties;
    // absent keys → hasReleaseSigningConfig=false → no signingConfig assigned →
    // build succeeds (unsigned) without crashing.
    if (hasReleaseSigningConfig) {
        signingConfigs {
            create("release") {
                storeFile     = file(releaseKeystorePath!!)
                keyAlias      = releaseKeyAlias!!
                storePassword = releaseStorePassword!!
                keyPassword   = releaseKeyPassword!!
            }
        }
    }

    // ── Build types ───────────────────────────────────────────────────────
    buildTypes {
        debug {
            isDebuggable          = true
            isMinifyEnabled       = false
            applicationIdSuffix   = ".debug"
            versionNameSuffix     = "-debug"
            buildConfigField("Boolean", "ENABLE_LOGGING", "true")
        }
        release {
            isMinifyEnabled       = true
            isShrinkResources     = true
            isDebuggable          = false
            buildConfigField("Boolean", "ENABLE_LOGGING", "false")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // Wire signing only when local.properties supplies all four
            // release.* keys (see top of file). Absent → unsigned release
            // build (still installable via adb for local testing).
            if (hasReleaseSigningConfig) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    // ── Java / Kotlin compatibility ───────────────────────────────────────
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs += listOf(
            "-opt-in=kotlin.RequiresOptIn",
            "-opt-in=kotlinx.coroutines.ExperimentalCoroutinesApi",
            "-opt-in=kotlinx.coroutines.FlowPreview",
            "-opt-in=androidx.compose.material3.ExperimentalMaterial3Api",
            "-opt-in=androidx.compose.foundation.ExperimentalFoundationApi",
            "-opt-in=androidx.compose.animation.ExperimentalAnimationApi",
            "-opt-in=androidx.compose.ui.ExperimentalComposeUiApi",
        )
    }

    // ── Build features ────────────────────────────────────────────────────
    buildFeatures {
        compose     = true
        buildConfig = true
        // Disable unused generators — faster build times
        viewBinding = false
        dataBinding = false
        aidl        = false
        renderScript = false
    }

    // ── Packaging ─────────────────────────────────────────────────────────
    packaging {
        resources {
            excludes += setOf(
                "META-INF/LICENSE.md",
                "META-INF/LICENSE-notice.md",
                "META-INF/NOTICE.md",
                "META-INF/*.kotlin_module",
                "META-INF/AL2.0",
                "META-INF/LGPL2.1",
                // Protobuf ships duplicate descriptor files across artifacts
                "google/protobuf/*.proto",
            )
        }
        // Keep both arm64 and x86_64 .so files from libsu / PTY bridge
        jniLibs {
            useLegacyPackaging = false
        }
    }

    // ── Lint ──────────────────────────────────────────────────────────────
    lint {
        abortOnError   = true
        warningsAsErrors = false
        disable        += setOf(
            "ObsoleteLintCustomCheck",
            "UnusedResources",  // resources generated by Compose tooling
        )
        // API key patterns — never allow leaking into lint reports
        textReport     = false
        htmlReport     = false
    }

    // ── Test options (JUnit 5 via android-junit5 plugin) ──────────────────
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            isReturnDefaultValues     = true
            all {
                it.useJUnitPlatform()
            }
        }
        animationsDisabled = true
    }

    // ── Schema source sets (Room migration files) ─────────────────────────
    sourceSets {
        getByName("androidTest") {
            assets.srcDirs("$projectDir/schemas")
        }
    }
}

// ── Protobuf (DataStore Proto typed settings) ─────────────────────────────
protobuf {
    protoc {
        artifact = libs.protobuf.protoc.get().toString()
    }
    generateProtoTasks {
        all().forEach { task ->
            task.builtins {
                id("java") { option("lite") }
                id("kotlin") { option("lite") }
            }
        }
    }
}

// ── Duplicate class exclusions ────────────────────────────────────────────
configurations.all {
    // Force modern Android-variant guava — it no longer bundles ListenableFuture,
    // so listenablefuture-1.0 provides the class without any duplicate.
    resolutionStrategy.eachDependency {
        if (requested.group == "com.google.guava" && requested.name == "guava") {
            useVersion("33.4.0-android")
        }
    }
    // prism4j-bundler is an annotation processor — exclude from runtime to avoid duplicate annotation classes
    exclude(group = "io.noties", module = "prism4j-bundler")
    // annotations:23 supersedes the legacy annotations-java5 artifact
    exclude(group = "org.jetbrains", module = "annotations-java5")
    // dadb ships BouncyCastle — Android provides its own; exclude to avoid duplicate-class errors
    exclude(group = "org.bouncycastle")
}

// ── Dependencies ──────────────────────────────────────────────────────────
dependencies {

    // ── Kotlin ────────────────────────────────────────────────────────────
    implementation(libs.kotlin.stdlib)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.kotlinx.coroutines.android)

    // ── AndroidX Core ─────────────────────────────────────────────────────
    implementation(libs.androidx.core.ktx)
    implementation(libs.bundles.lifecycle)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.fragment.ktx)
    implementation(libs.androidx.splashscreen)
    implementation(libs.androidx.startup)
    implementation(libs.androidx.window)
    implementation(libs.androidx.work.runtime)
    // Pulls a 16 KB-aligned libandroidx.graphics.path.so to override the
    // unaligned transitive version that Compose otherwise brings in.
    implementation(libs.androidx.graphics.path)

    // ── Compose ───────────────────────────────────────────────────────────
    implementation(platform(libs.compose.bom))
    implementation(libs.bundles.compose.ui)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)

    // ── Navigation ────────────────────────────────────────────────────────
    implementation(libs.navigation.compose)

    // ── Hilt (DI) ─────────────────────────────────────────────────────────
    implementation(libs.hilt.android)
    implementation(libs.hilt.navigation.compose)
    implementation(libs.hilt.work)
    ksp(libs.hilt.compiler)
    ksp(libs.hilt.work.compiler)

    // ── Networking (Claude API + SSE) ─────────────────────────────────────
    implementation(libs.bundles.networking)

    // ── Room (chat history, command history) ──────────────────────────────
    implementation(libs.bundles.room)
    ksp(libs.room.compiler)

    // ── DataStore Proto (typed settings) ──────────────────────────────────
    implementation(libs.datastore.core)
    implementation(libs.datastore.preferences)
    implementation(libs.protobuf.kotlin.lite)

    // ── Security ──────────────────────────────────────────────────────────
    implementation(libs.security.crypto)      // EncryptedSharedPreferences (API key)
    implementation(libs.biometric)            // biometric gate for settings

    // ── Image Loading ─────────────────────────────────────────────────────
    implementation(libs.coil.compose)
    implementation(libs.coil.network.okhttp)

    // ── Camera ────────────────────────────────────────────────────────────
    implementation(libs.bundles.camerax)

    // ── Location ──────────────────────────────────────────────────────────
    implementation(libs.play.services.location)

    // ── Maps (OpenStreetMap — no account required) ─────────────────────────
    implementation(libs.osmdroid.android)

    // ── Root Shell (libsu) ────────────────────────────────────────────────
    implementation(libs.bundles.libsu)

    // ── ADB on-device client (dadb) ───────────────────────────────────────
    // Lets JARVIS connect to local adbd via localhost:5555 and run shell
    // commands with `adb shell` privileges — no root needed once
    // Wireless Debugging is enabled in Developer Options.
    implementation(libs.dadb)

    // ── Local LLM (LiteRT-LM — Google AI Edge on-device) ─────────────────
    // Powers the Gemma / Qwen / DeepSeek-R1 Distill entries in the catalog.
    // CPU / GPU / NPU backends are chosen per-model (see LiteRtLmBackend).
    implementation(libs.litertlm)

    // ── Markdown + Syntax Highlighting ────────────────────────────────────
    implementation(libs.bundles.markwon)

    // ── Document Extraction (PDF → chat input) ───────────────────────────
    implementation(libs.pdfbox.android)

    // ── Accompanist ───────────────────────────────────────────────────────
    implementation(libs.accompanist.systemuicontroller)
    implementation(libs.accompanist.permissions)   // batch dangerous perm requests

    // ── Unit Tests ────────────────────────────────────────────────────────
    testImplementation(libs.bundles.testing.unit)
    testRuntimeOnly(libs.junit5.engine)
    testRuntimeOnly(libs.junit.vintage.engine)

    // ── Instrumented (Android) Tests ──────────────────────────────────────
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.bundles.testing.android)
    androidTestRuntimeOnly(libs.junit5.engine)
}
