package com.elder.companion

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import com.elder.companion.ui.App

class MainActivity : ComponentActivity() {

    private val requestMic =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* 拒绝则通话时报错提示 */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ensureMicPermission()
        setContent { App() }
    }

    private fun ensureMicPermission() {
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) requestMic.launch(Manifest.permission.RECORD_AUDIO)
    }
}
