package com.remotelink.app

import android.content.Context

/** 应用默认设置（画质 / 帧率 / 横屏 / 自动连接）。 */
object Prefs {
    private const val NAME = "remotelink_settings"

    private fun sp(ctx: Context) = ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun quality(ctx: Context): String = sp(ctx).getString("quality", "original") ?: "original"
    fun fps(ctx: Context): Int = sp(ctx).getInt("fps", 30)
    fun landscape(ctx: Context): Boolean = sp(ctx).getBoolean("landscape", false)
    fun autoConnect(ctx: Context): Boolean = sp(ctx).getBoolean("autoConnect", false)

    fun setQuality(ctx: Context, v: String) = sp(ctx).edit().putString("quality", v).apply()
    fun setFps(ctx: Context, v: Int) = sp(ctx).edit().putInt("fps", v).apply()
    fun setLandscape(ctx: Context, v: Boolean) = sp(ctx).edit().putBoolean("landscape", v).apply()
    fun setAutoConnect(ctx: Context, v: Boolean) = sp(ctx).edit().putBoolean("autoConnect", v).apply()
}
