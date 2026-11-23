package com.remotelink.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** 设备列表的本地持久化（SharedPreferences + JSON）。 */
object DeviceStore {
    private const val PREFS = "remotelink_devices"
    private const val KEY = "devices"

    fun list(ctx: Context): List<Device> {
        val raw = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY, "[]") ?: "[]"
        return try {
            val arr = JSONArray(raw)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Device(o.getString("id"), o.getString("name"), o.getString("url"))
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun save(ctx: Context, devices: List<Device>) {
        val arr = JSONArray()
        for (d in devices) {
            arr.put(JSONObject().apply {
                put("id", d.id)
                put("name", d.name)
                put("url", d.url)
            })
        }
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY, arr.toString()).apply()
    }

    fun add(ctx: Context, device: Device) {
        val list = list(ctx).toMutableList()
        list.add(0, device)
        save(ctx, list)
    }

    fun remove(ctx: Context, id: String) {
        save(ctx, list(ctx).filter { it.id != id })
    }

    fun update(ctx: Context, device: Device) {
        save(ctx, list(ctx).map { if (it.id == device.id) device else it })
    }
}
