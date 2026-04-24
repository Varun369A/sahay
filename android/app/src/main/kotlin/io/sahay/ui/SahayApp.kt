package io.sahay.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.res.stringResource
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import io.sahay.R
import io.sahay.service.SahayAccessibilityService
import io.sahay.ui.screens.auth.AuthScreen
import io.sahay.ui.screens.auth.AuthViewModel
import io.sahay.ui.screens.files.FilesScreen
import io.sahay.ui.screens.home.HomeScreen
import io.sahay.ui.screens.logs.LogsScreen
import io.sahay.ui.screens.permissions.PermissionsScreen
import io.sahay.ui.screens.settings.SettingsScreen
import io.sahay.util.Permissions

sealed class Route(val path: String) {
    data object Home : Route("home")
    data object Logs : Route("logs")
    data object Files : Route("files")
    data object Settings : Route("settings")
}

private data class TabSpec(val route: Route, val labelRes: Int, val icon: ImageVector)

private val Tabs = listOf(
    TabSpec(Route.Home, R.string.tab_home, Icons.Default.Home),
    TabSpec(Route.Logs, R.string.tab_logs, Icons.Default.Description),
    TabSpec(Route.Files, R.string.tab_files, Icons.Default.Folder),
    TabSpec(Route.Settings, R.string.tab_settings, Icons.Default.Settings),
)

/**
 * App-wide gate. Three states (in order):
 *   1. Not authenticated  → AuthScreen (email + OTP)
 *   2. Authenticated but missing permissions → PermissionsScreen
 *   3. Authenticated AND all permissions granted → main NavHost
 *
 * The permission check happens on every onResume, so when the user comes
 * back from the system Accessibility settings, we automatically advance.
 */
@Composable
fun SahayApp() {
    val authVm: AuthViewModel = hiltViewModel()
    val authState by authVm.state.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle

    val a11yServiceFqcn = SahayAccessibilityService::class.java.name

    var permsGranted by remember {
        mutableStateOf(Permissions.allGranted(context, a11yServiceFqcn))
    }

    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                permsGranted = Permissions.allGranted(context, a11yServiceFqcn)
            }
        }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }

    when {
        !authState.isAuthenticated -> AuthScreen(viewModel = authVm)
        !permsGranted -> PermissionsScreen(onAllGranted = { permsGranted = true })
        else -> MainShell(onLoggedOut = { authVm.logout() })
    }
}

@Composable
private fun MainShell(onLoggedOut: () -> Unit) {
    val navController = rememberNavController()
    val backStack by navController.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route

    Scaffold(
        bottomBar = {
            if (currentRoute in Tabs.map { it.route.path }) {
                NavigationBar {
                    Tabs.forEach { tab ->
                        NavigationBarItem(
                            selected = backStack?.destination?.hierarchy?.any { it.route == tab.route.path } == true,
                            onClick = {
                                navController.navigate(tab.route.path) {
                                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(tab.icon, contentDescription = null) },
                            label = { Text(stringResource(tab.labelRes)) },
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Route.Home.path,
            modifier = Modifier.padding(padding),
        ) {
            composable(Route.Home.path) { HomeScreen() }
            composable(Route.Logs.path) { LogsScreen() }
            composable(Route.Files.path) { FilesScreen() }
            composable(Route.Settings.path) {
                SettingsScreen(onLoggedOut = onLoggedOut)
            }
        }
    }
}
