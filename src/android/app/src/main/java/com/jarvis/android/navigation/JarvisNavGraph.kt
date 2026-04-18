package com.jarvis.android.navigation

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.jarvis.android.presentation.builder.AppBuilderScreen
import com.jarvis.android.presentation.chat.ChatScreen
import com.jarvis.android.presentation.cyber.CyberScreen
import com.jarvis.android.presentation.filesystem.FileManagerScreen
import com.jarvis.android.presentation.localai.LocalAiScreen
import com.jarvis.android.presentation.network.NetworkScreen
import com.jarvis.android.presentation.onboarding.OnboardingScreen
import com.jarvis.android.presentation.permissions.PermissionMatrixScreen
import com.jarvis.android.presentation.root.RootInstallerScreen
import com.jarvis.android.presentation.sensors.SensorDashboardScreen
import com.jarvis.android.presentation.settings.SettingsScreen
import com.jarvis.android.presentation.system.SystemDashboardScreen
import com.jarvis.android.presentation.terminal.TerminalScreen

private const val PREF_FILE       = "jarvis_app"
private const val KEY_ONBOARDING  = "onboarding_done"

/**
 * Root navigation host for the JARVIS app.
 *
 * Start destination is determined at runtime:
 *   - If onboarding has never been completed → [Screen.Onboarding]
 *   - Otherwise → [Screen.Chat] (default conversationId = "default")
 *
 * Navigation conventions:
 *   - Every leaf screen receives an `onBack` lambda that calls [NavHostController.popBackStack].
 *   - The Chat screen navigates to other tools via its toolbar/drawer overflow menu
 *     by calling the route helpers on the [Screen] sealed class.
 *   - The Onboarding flow calls [onOnboardingComplete] which marks the pref and
 *     navigates to Chat while clearing the back stack so Back doesn't return to Onboarding.
 */
@Composable
fun JarvisNavGraph(
    context:     Context,
    modifier:    Modifier          = Modifier,
    navController: NavHostController = rememberNavController(),
) {
    val onboardingDone = context
        .getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
        .getBoolean(KEY_ONBOARDING, false)

    val startDestination = if (onboardingDone) Screen.Chat.route("default")
                           else Screen.Onboarding.route

    NavHost(
        navController    = navController,
        startDestination = startDestination,
        modifier         = modifier,
    ) {

        // ── Onboarding ─────────────────────────────────────────────────────

        composable(Screen.Onboarding.route) {
            OnboardingScreen(
                onFinish      = {
                    markOnboardingDone(context)
                    navController.navigate(Screen.Chat.route("default")) {
                        popUpTo(Screen.Onboarding.route) { inclusive = true }
                    }
                },
                onPermissions = {
                    navController.navigate(Screen.Permissions.route)
                },
            )
        }

        // ── Chat ───────────────────────────────────────────────────────────

        composable(
            route     = Screen.Chat.route,
            arguments = listOf(
                navArgument(Screen.Chat.ARG_CONVERSATION_ID) {
                    type         = NavType.StringType
                    defaultValue = "default"
                },
            ),
        ) {
            ChatScreen(
                onNavigateToTerminal    = { navController.navigate(Screen.Terminal.route) },
                onNavigateToFiles       = { navController.navigate(Screen.FileManager.route) },
                onNavigateToSystem      = { navController.navigate(Screen.SystemDashboard.route) },
                onNavigateToNetwork     = { navController.navigate(Screen.Network.route) },
                onNavigateToSensors     = { navController.navigate(Screen.SensorDashboard.route) },
                onNavigateToPermissions = { navController.navigate(Screen.Permissions.route) },
                onNavigateToSettings    = { navController.navigate(Screen.Settings.route) },
                onNavigateToLocalAi     = { navController.navigate(Screen.LocalAi.route) },
                onNavigateToAppBuilder  = { navController.navigate(Screen.AppBuilder.route) },
                onNavigateToCyberSuite  = { navController.navigate(Screen.CyberSuite.route) },
            )
        }

        // ── Tool screens ───────────────────────────────────────────────────

        composable(Screen.LocalAi.route) {
            LocalAiScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.AppBuilder.route) {
            AppBuilderScreen(
                onBack      = { navController.popBackStack() },
                onLaunchApp = { path ->
                    // WebView apps: launch via AppRunnerActivity; shell/python open in terminal
                    navController.navigate(Screen.Terminal.route)
                },
            )
        }

        composable(Screen.CyberSuite.route) {
            CyberScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.Terminal.route) {
            TerminalScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.FileManager.route) {
            FileManagerScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.SystemDashboard.route) {
            SystemDashboardScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.Network.route) {
            NetworkScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.SensorDashboard.route) {
            SensorDashboardScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.Permissions.route) {
            PermissionMatrixScreen(
                onBack        = { navController.popBackStack() },
                onInstallRoot = { navController.navigate(Screen.RootInstaller.route) },
            )
        }

        composable(Screen.Settings.route) {
            SettingsScreen(onBack = { navController.popBackStack() })
        }

        composable(Screen.RootInstaller.route) {
            RootInstallerScreen(onBack = { navController.popBackStack() })
        }
    }
}

private fun markOnboardingDone(context: Context) {
    context.getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
        .edit()
        .putBoolean(KEY_ONBOARDING, true)
        .apply()
}
