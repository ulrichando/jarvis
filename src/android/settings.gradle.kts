pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // JitPack — required for libsu
        maven {
            url = uri("https://jitpack.io")
            content {
                includeGroup("com.github.topjohnwu.libsu")
            }
        }
    }
}

rootProject.name = "jarvis-android"
include(":app")
