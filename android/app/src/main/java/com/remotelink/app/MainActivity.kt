package com.remotelink.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import java.util.UUID

/** 主页：设备列表 + 默认设置。 */
class MainActivity : Activity() {

    private lateinit var deviceList: LinearLayout
    private lateinit var emptyHint: TextView
    private lateinit var qualityGroup: RadioGroup
    private lateinit var fpsGroup: RadioGroup
    private lateinit var landscapeCheck: CheckBox
    private lateinit var autoConnectCheck: CheckBox

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        deviceList = findViewById(R.id.deviceList)
        emptyHint = findViewById(R.id.emptyHint)
        qualityGroup = findViewById(R.id.qualityGroup)
        fpsGroup = findViewById(R.id.fpsGroup)
        landscapeCheck = findViewById(R.id.landscapeCheck)
        autoConnectCheck = findViewById(R.id.autoConnectCheck)

        // 先回填当前设置，再挂监听，避免初始化时误触发保存
        qualityGroup.check(when (Prefs.quality(this)) {
            "hd" -> R.id.qHd
            "smooth" -> R.id.qSmooth
            else -> R.id.qOriginal
        })
        fpsGroup.check(when (Prefs.fps(this)) {
            60 -> R.id.f60
            15 -> R.id.f15
            else -> R.id.f30
        })
        landscapeCheck.isChecked = Prefs.landscape(this)
        autoConnectCheck.isChecked = Prefs.autoConnect(this)

        qualityGroup.setOnCheckedChangeListener { _, _ -> saveSettings() }
        fpsGroup.setOnCheckedChangeListener { _, _ -> saveSettings() }
        landscapeCheck.setOnCheckedChangeListener { _, _ -> saveSettings() }
        autoConnectCheck.setOnCheckedChangeListener { _, _ -> saveSettings() }

        findViewById<Button>(R.id.addBtn).setOnClickListener { showAddDialog() }

        render()

        if (autoConnectCheck.isChecked) {
            DeviceStore.list(this).firstOrNull()?.let { connect(it) }
        }
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun saveSettings() {
        Prefs.setQuality(this, when (qualityGroup.checkedRadioButtonId) {
            R.id.qHd -> "hd"
            R.id.qSmooth -> "smooth"
            else -> "original"
        })
        Prefs.setFps(this, when (fpsGroup.checkedRadioButtonId) {
            R.id.f60 -> 60
            R.id.f15 -> 15
            else -> 30
        })
        Prefs.setLandscape(this, landscapeCheck.isChecked)
        Prefs.setAutoConnect(this, autoConnectCheck.isChecked)
    }

    private fun render() {
        val devices = DeviceStore.list(this)
        deviceList.removeAllViews()
        emptyHint.visibility = if (devices.isEmpty()) View.VISIBLE else View.GONE
        for (d in devices) {
            deviceList.addView(buildRow(d))
        }
    }

    private fun buildRow(device: Device): View {
        val row = layoutInflater.inflate(R.layout.item_device, deviceList, false)
        row.findViewById<TextView>(R.id.deviceName).text = device.name
        row.findViewById<TextView>(R.id.deviceHost).text = device.url
        row.setOnClickListener { connect(device) }
        row.setOnLongClickListener { showAddDialog(device); true }
        row.findViewById<Button>(R.id.deviceDelete).setOnClickListener {
            DeviceStore.remove(this, device.id)
            render()
            Toast.makeText(this, "已删除 ${device.name}", Toast.LENGTH_SHORT).show()
        }
        return row
    }

    private fun connect(device: Device) {
        startActivity(Intent(this, ControlActivity::class.java)
            .putExtra("url", device.url))
    }

    private fun showAddDialog(existing: Device? = null) {
        val nameInput = EditText(this).apply {
            hint = "名称（可选，默认取域名）"
            setText(existing?.name ?: "")
        }
        val urlInput = EditText(this).apply {
            hint = "链接，如 https://xxx/?k=口令"
            setText(existing?.url ?: "")
            inputType = InputType.TYPE_TEXT_VARIATION_URI
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(12), dp(20), 0)
            addView(nameInput)
            addView(urlInput)
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle(if (existing == null) "添加设备" else "编辑设备")
            .setView(box)
            .setPositiveButton("保存", null)
            .setNegativeButton("取消", null)
            .create()
        dialog.show()
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val url = normalize(urlInput.text.toString())
            if (url.isEmpty()) {
                Toast.makeText(this, "请填写有效链接", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val name = nameInput.text.toString().trim().ifEmpty { hostOf(url) }
            if (existing == null) {
                DeviceStore.add(this, Device(UUID.randomUUID().toString(), name, url))
            } else {
                DeviceStore.update(this, existing.copy(name = name, url = url))
            }
            render()
            dialog.dismiss()
        }
    }

    private fun normalize(raw: String): String {
        var v = raw.trim()
        if (v.isEmpty()) return ""
        if (!v.startsWith("http://") && !v.startsWith("https://")) v = "https://$v"
        return v
    }

    private fun hostOf(url: String): String = try {
        url.substringAfter("://").substringBefore("/").substringBefore("?")
    } catch (_: Exception) {
        url
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
