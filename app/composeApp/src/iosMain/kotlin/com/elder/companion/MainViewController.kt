package com.elder.companion

import androidx.compose.ui.window.ComposeUIViewController
import com.elder.companion.ui.App
import platform.UIKit.UIViewController

/** iOS 入口：供 Swift 侧 SwiftUI/UIKit 包装为根视图控制器。 */
fun MainViewController(): UIViewController = ComposeUIViewController { App() }
