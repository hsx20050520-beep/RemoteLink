package com.remotelink.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.pm.ActivityInfo
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient

/** 沉浸式控制页：全屏 WebView 加载远程桌面，支持横屏锁定与常亮。 */
class ControlActivity : Activity() {

    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_control)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        web = findViewById(R.id.webView)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            allowFileAccess = false
            allowContentAccess = false
            cacheMode = WebSettings.LOAD_NO_CACHE
            userAgentString = "$userAgentString RemoteLinkApp/1.0"
        }
        web.setBackgroundColor(0xFF000000.toInt())
        web.webViewClient = WebViewClient()
        web.webChromeClient = WebChromeClient()
        web.keepScreenOn = true

        if (Prefs.landscape(this)) {
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        }

        val url = intent.getStringExtra("url").orEmpty()
        web.loadUrl(buildUrl(url))
        immersive()
    }

    /** 把默认画质/帧率/显示器作为 URL 参数传给 Web 端。 */
    private fun buildUrl(url: String): String {
        if (url.isEmpty()) return "about:blank"
        val quality = Prefs.quality(this)
        val fps = Prefs.fps(this)
        val sep = if (url.contains('?')) '&' else '?'
        return "$url${sep}quality=$quality&fps=$fps&monitor=1"
    }

    @Suppress("DEPRECATION")
    private fun immersive() {
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        )
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) immersive()
    }

    @Deprecated("返回主页")
    override fun onBackPressed() {
        web.loadUrl("about:blank")
        finish()
    }

    override fun onPause() {
        super.onPause()
        web.onPause()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        immersive()
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }
}
