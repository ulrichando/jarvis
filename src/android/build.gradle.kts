// Top-level build file — configuration here applies to all subprojects/modules.
// Per-module dependencies go in app/build.gradle.kts, not here.

plugins {
    alias(libs.plugins.android.application)  apply false
    alias(libs.plugins.android.library)      apply false
    alias(libs.plugins.kotlin.android)       apply false
    alias(libs.plugins.kotlin.compose)       apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.kotlin.parcelize)     apply false
    alias(libs.plugins.ksp)                  apply false
    alias(libs.plugins.hilt)                 apply false
    alias(libs.plugins.protobuf)             apply false
    alias(libs.plugins.android.junit5)       apply false
}
