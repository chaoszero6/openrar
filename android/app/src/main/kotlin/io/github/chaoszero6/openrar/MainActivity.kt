package io.github.chaoszero6.openrar

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalView
import io.github.chaoszero6.openrar.ui.ArchiveScreen
import io.github.chaoszero6.openrar.ui.DialogHost
import io.github.chaoszero6.openrar.ui.FilesScreen
import io.github.chaoszero6.openrar.ui.OpenRarTheme
import io.github.chaoszero6.openrar.ui.ProgressDialog

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    private val legacyPermission = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        vm.onResume()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) vm.handleIntent(intent)
        setContent {
            OpenRarTheme {
                val snackbar = remember { SnackbarHostState() }
                var about by remember { mutableStateOf(false) }
                LaunchedEffect(Unit) {
                    vm.messages.collect { m ->
                        val r = snackbar.showSnackbar(m.text, m.actionLabel, withDismissAction = m.actionLabel == null,
                            duration = if (m.actionLabel != null) SnackbarDuration.Long else SnackbarDuration.Short)
                        if (r == SnackbarResult.ActionPerformed) m.action?.invoke()
                    }
                }
                BackHandler(enabled = vm.archive != null || vm.selectedFiles.isNotEmpty() || vm.canGoUp()) {
                    when {
                        vm.archive != null && vm.selectedEntries.isNotEmpty() -> vm.selectedEntries.clear()
                        vm.archive != null -> if (!vm.folderUp()) vm.closeArchive()
                        vm.selectedFiles.isNotEmpty() -> vm.selectedFiles.clear()
                        else -> vm.navigate(vm.dir.parentFile!!)
                    }
                }
                if (vm.archive != null) ArchiveScreen(vm, snackbar)
                else FilesScreen(vm, snackbar, onRequestAccess = ::requestAccess, onAbout = { about = true })
                val view = LocalView.current
                val t = vm.task
                view.keepScreenOn = t != null
                // a question from the running task (password, overwrite) must not sit underneath the progress dialog
                if (t != null && vm.dialog == null) ProgressDialog(t) { vm.cancelTask() }
                DialogHost(vm)
                if (about) AlertDialog(
                    onDismissRequest = { about = false },
                    title = { Text("OpenRAR ${BuildConfigCompat.versionName(this)}") },
                    text = {
                        Text("Free archive manager. Opens ZIP, 7Z (incl. AES), RAR (incl. RAR5), TAR, GZ, BZ2, XZ, split " +
                            "volumes and OpenRAR self-extracting archives; creates ZIP (AES-256), 7Z and TAR archives.\n\n" +
                            "MIT License. Uses zip4j, Apache Commons Compress, XZ for Java, junrar and " +
                            "7-Zip-JBinding (7-Zip by Igor Pavlov, LGPL; RAR decoding uses unRAR code by Alexander Roshal).\n" +
                            "github.com/chaoszero6/openrar")
                    },
                    confirmButton = { TextButton(onClick = { about = false }) { Text("Close") } },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        vm.handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        vm.onResume()
    }

    private fun requestAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            try {
                startActivity(Storage.accessSettingsIntent(this))
            } catch (_: Exception) {
                startActivity(Intent(android.provider.Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
            }
        } else {
            legacyPermission.launch(arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE))
        }
    }
}

object BuildConfigCompat {
    fun versionName(a: ComponentActivity): String =
        a.packageManager.getPackageInfo(a.packageName, 0).versionName ?: ""
}
