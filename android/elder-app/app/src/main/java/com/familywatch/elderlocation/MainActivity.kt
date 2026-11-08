package com.familywatch.elderlocation

import android.Manifest
import android.app.Application
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewModelScope
import com.familywatch.elderlocation.data.AppConfig
import com.familywatch.elderlocation.data.networkErrorMessage
import com.familywatch.elderlocation.location.LocationService
import com.familywatch.elderlocation.recovery.RecoveryWorker
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.util.Date

private val DefaultServerUrl = if (BuildConfig.DEBUG) "http://10.0.2.2:18763" else ""
private val Primary = Color(0xFF076B61)
private val PrimaryDark = Color(0xFF07544D)
private val Background = Color(0xFFF4F7F6)
private val SurfaceSubtle = Color(0xFFF8FAF9)
private val TextPrimary = Color(0xFF142924)
private val TextMuted = Color(0xFF667873)
private val SuccessSoft = Color(0xFFE7F4EF)
private val WarningSoft = Color(0xFFFFF4DE)
private val Danger = Color(0xFFB52E3B)

class MainActivity : ComponentActivity() {
    private val model: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                colorScheme = lightColorScheme(
                    primary = Primary,
                    onPrimary = Color.White,
                    background = Background,
                    surface = Color.White,
                    onSurface = TextPrimary,
                    error = Danger,
                ),
            ) {
                Surface(Modifier.fillMaxSize(), color = Background) { ElderApp(model, this) }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        model.ensureService()
    }
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as ElderApplication
    val config = app.container.config.config
    val pendingCount = app.container.database.locations().observeCount()
    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()
    private val _justEnrolled = MutableStateFlow(false)
    val justEnrolled: StateFlow<Boolean> = _justEnrolled.asStateFlow()
    private val _syncMessage = MutableStateFlow<String?>(null)
    val syncMessage: StateFlow<String?> = _syncMessage.asStateFlow()

    fun enroll(serverUrl: String, code: String, pin: String) = viewModelScope.launch {
        _busy.value = true
        _error.value = null
        runCatching { app.container.locations.enroll(serverUrl, code, pin) }
            .onSuccess {
                ContextCompat.startForegroundService(app, Intent(app, LocationService::class.java))
                RecoveryWorker.schedule(app)
                _justEnrolled.value = true
            }
            .onFailure { _error.value = networkErrorMessage(it) }
        _busy.value = false
    }

    fun acknowledgeEnrollment() { _justEnrolled.value = false }
    fun networkLabel() = app.container.locations.networkLabel() ?: "无可用网络"
    fun backgroundLocationGranted() = app.container.locations.backgroundLocationGranted()
    fun notificationGranted() = app.container.locations.notificationGranted()
    fun batteryOptimizationIgnored() = app.container.locations.batteryOptimizationIgnored()
    fun locationServicesEnabled() = app.container.locations.locationServicesEnabled()
    suspend fun verifyPin(pin: String) = app.container.locations.verifyPin(pin)

    fun ensureService() = viewModelScope.launch {
        if (!app.container.config.config.first().enrolled) return@launch
        runCatching { ContextCompat.startForegroundService(app, Intent(app, LocationService::class.java)) }
            .onFailure { app.container.config.recordServiceError("service_start_error") }
        RecoveryWorker.schedule(app)
    }

    fun syncConfig() = viewModelScope.launch {
        _syncMessage.value = "正在同步…"
        runCatching { app.container.locations.refreshConfig() }
            .onSuccess { _syncMessage.value = "住宅配置已同步" }
            .onFailure { _syncMessage.value = networkErrorMessage(it) }
    }

    fun unbind() = viewModelScope.launch {
        app.container.locations.clear()
        app.stopService(Intent(app, LocationService::class.java))
        app.synchronizeStop()
        _justEnrolled.value = false
    }
}

@Composable
private fun ElderApp(model: MainViewModel, activity: MainActivity) {
    val config by model.config.collectAsState(initial = AppConfig())
    val busy by model.busy.collectAsState()
    val error by model.error.collectAsState()
    val justEnrolled by model.justEnrolled.collectAsState()
    when {
        justEnrolled -> BindingSuccessScreen(config, model::acknowledgeEnrollment)
        !config.enrolled -> SetupScreen(model, activity, busy, error, config.serverUrl.isNotBlank())
        else -> StatusScreen(model, activity, config)
    }
}

@Composable
private fun SetupScreen(model: MainViewModel, activity: MainActivity, busy: Boolean, error: String?, pendingStop: Boolean) {
    var serverUrl by remember { mutableStateOf(DefaultServerUrl) }
    var showAdvanced by remember { mutableStateOf(false) }
    var code by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var privacyAccepted by remember { mutableStateOf(false) }
    var permissionTick by remember { mutableIntStateOf(0) }
    val foregroundLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permissionTick++ }
    val backgroundLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { permissionTick++ }
    DisposableEffect(activity) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) permissionTick++ }
        activity.lifecycle.addObserver(observer)
        onDispose { activity.lifecycle.removeObserver(observer) }
    }
    @Suppress("UNUSED_EXPRESSION") permissionTick
    val foregroundGranted = ActivityCompat.checkSelfPermission(activity, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
    val backgroundGranted = Build.VERSION.SDK_INT < 30 || ActivityCompat.checkSelfPermission(activity, Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED
    val notificationGranted = Build.VERSION.SDK_INT < 33 || ActivityCompat.checkSelfPermission(activity, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
    val network = model.networkLabel()

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Column(Modifier.fillMaxWidth()) {
            if (pendingStop) Text("本机已停止共享，正在通知家人。", color = Primary)
            Text("老人端 · 首次设置", color = Primary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Text("连接家人守护", fontSize = 28.sp, fontWeight = FontWeight.Bold, color = TextPrimary, modifier = Modifier.padding(top = 5.dp))
            Text("请由家属完成一次设置。绑定后，老人平时无需操作手机。", color = TextMuted, fontSize = 14.sp, lineHeight = 21.sp, modifier = Modifier.padding(top = 7.dp))
        }
        Spacer(Modifier.height(20.dp))
        AppCard {
            SectionTitle("01", "服务器与网络")
            InfoLine("服务器", serverUrl.removePrefix("https://"), true)
            HorizontalDivider(color = Color(0xFFE7ECEA))
            InfoLine("当前网络", network, network != "无可用网络")
            TextButton(onClick = { showAdvanced = !showAdvanced }, modifier = Modifier.align(Alignment.End)) {
                Text(if (showAdvanced) "收起高级设置" else "高级设置")
            }
            if (showAdvanced) OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it.trim() },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("HTTPS 服务器地址") },
                singleLine = true,
            )
        }
        Spacer(Modifier.height(12.dp))
        AppCard {
            SectionTitle("02", "输入绑定信息")
            OutlinedTextField(
                code,
                { code = it.filter(Char::isDigit).take(6) },
                Modifier.fillMaxWidth(),
                label = { Text("六位绑定码") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                pin,
                { pin = it.filter(Char::isDigit).take(6) },
                Modifier.fillMaxWidth(),
                label = { Text("设置六位家属管理 PIN") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
            )
        }
        Spacer(Modifier.height(12.dp))
        AppCard {
            SectionTitle("03", "系统权限")
            PermissionRow("精确位置", foregroundGranted) {
                foregroundLauncher.launch(buildList {
                    add(Manifest.permission.ACCESS_FINE_LOCATION)
                    add(Manifest.permission.ACCESS_COARSE_LOCATION)
                    if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
                }.toTypedArray())
            }
            PermissionRow("始终允许后台定位", backgroundGranted) { backgroundLauncher.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION) }
            PermissionRow("静音常驻通知", notificationGranted) { if (Build.VERSION.SDK_INT >= 33) foregroundLauncher.launch(arrayOf(Manifest.permission.POST_NOTIFICATIONS)) }
            Row(verticalAlignment = Alignment.Top, modifier = Modifier.padding(top = 8.dp)) {
                Checkbox(privacyAccepted, { privacyAccepted = it })
                Text("我同意向绑定的家人共享位置、精度和电量。锁屏后仍会共享，我可以随时停止。", modifier = Modifier.padding(top = 10.dp), fontSize = 13.sp, lineHeight = 19.sp, color = TextMuted)
            }
        }
        if (error != null) {
            Surface(color = Color(0xFFFFEFF1), shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth().padding(top = 12.dp)) {
                Text(error, color = Danger, fontSize = 13.sp, lineHeight = 19.sp, modifier = Modifier.padding(13.dp))
            }
        }
        Button(
            onClick = { model.enroll(serverUrl, code, pin) },
            enabled = !busy && (serverUrl.startsWith("https://") || (BuildConfig.DEBUG && serverUrl == DefaultServerUrl)) && code.length == 6 && pin.length == 6 && privacyAccepted && foregroundGranted && backgroundGranted && notificationGranted && network != "无可用网络",
            modifier = Modifier.fillMaxWidth().height(52.dp).padding(top = 12.dp),
        ) { Text(if (busy) "正在检查网络并绑定…" else "完成绑定并开始定位") }
        Text("绑定前会先检查服务器连通性。若“始终允许”未弹出，请在系统设置的应用权限中开启。", color = TextMuted, fontSize = 12.sp, lineHeight = 18.sp, modifier = Modifier.padding(top = 12.dp, bottom = 18.dp))
    }
}

@Composable
private fun AppCard(content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(16.dp), color = Color.White, shadowElevation = 1.dp, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(17.dp), content = content)
    }
}

@Composable
private fun SectionTitle(number: String, title: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 12.dp)) {
        Box(Modifier.size(30.dp).background(SuccessSoft, RoundedCornerShape(9.dp)), contentAlignment = Alignment.Center) {
            Text(number, color = Primary, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        }
        Text(title, fontWeight = FontWeight.Bold, fontSize = 17.sp, modifier = Modifier.padding(start = 10.dp))
    }
}

@Composable
private fun InfoLine(label: String, value: String, good: Boolean) {
    Row(Modifier.fillMaxWidth().padding(vertical = 9.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(if (good) Color(0xFF25805F) else Danger, CircleShape))
        Text(label, color = TextMuted, fontSize = 13.sp, modifier = Modifier.padding(start = 9.dp).weight(1f))
        Text(value, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun PermissionRow(title: String, granted: Boolean, request: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(if (granted) Color(0xFF25805F) else Color(0xFFC27A18), CircleShape))
        Text(title, Modifier.weight(1f).padding(horizontal = 10.dp), fontSize = 14.sp)
        if (!granted) OutlinedButton(onClick = request) { Text("授权") } else Text("已允许", color = Primary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun BindingSuccessScreen(config: AppConfig, acknowledge: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.size(76.dp).background(SuccessSoft, CircleShape), contentAlignment = Alignment.Center) {
            Text("✓", color = Primary, fontSize = 38.sp, fontWeight = FontWeight.Bold)
        }
        Text("绑定成功", fontSize = 28.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 22.dp))
        Text("${config.elderDisplayName.ifBlank { "老人手机" }}已连接家人守护", color = TextMuted, fontSize = 14.sp, modifier = Modifier.padding(top = 7.dp))
        Surface(color = SuccessSoft, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth().padding(top = 25.dp)) {
            Column(Modifier.padding(18.dp)) {
                Text("定位服务已启动", color = PrimaryDark, fontWeight = FontWeight.Bold)
                Text("手机会在后台记录位置和电量。家人在小程序里可以看到最近一次有效定位的采集时间。", color = TextMuted, fontSize = 13.sp, lineHeight = 20.sp, modifier = Modifier.padding(top = 6.dp))
            }
        }
        Button(onClick = acknowledge, modifier = Modifier.fillMaxWidth().height(52.dp).padding(top = 20.dp)) { Text("进入运行状态") }
    }
}

@Composable
private fun StatusScreen(model: MainViewModel, activity: MainActivity, config: AppConfig) {
    val pending by model.pendingCount.collectAsState(initial = 0)
    var manage by remember { mutableStateOf(false) }
    var permissionTick by remember { mutableIntStateOf(0) }
    DisposableEffect(activity) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) permissionTick++ }
        activity.lifecycle.addObserver(observer)
        onDispose { activity.lifecycle.removeObserver(observer) }
    }
    @Suppress("UNUSED_EXPRESSION") permissionTick
    val backgroundGranted = model.backgroundLocationGranted()
    val notificationGranted = model.notificationGranted()
    val batteryProtected = model.batteryOptimizationIgnored()
    val locationEnabled = model.locationServicesEnabled()
    val protectionReady = backgroundGranted && notificationGranted && batteryProtected && locationEnabled
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(22.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Spacer(Modifier.height(24.dp))
        Box(Modifier.size(68.dp).background(if (protectionReady) SuccessSoft else WarningSoft, CircleShape), contentAlignment = Alignment.Center) { Text(if (protectionReady) "✓" else "!", color = if (protectionReady) Primary else Color(0xFF8B5D17), fontSize = 32.sp, fontWeight = FontWeight.Bold) }
        Text(config.elderDisplayName, fontSize = 28.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 16.dp))
        Text(if (protectionReady) "后台守护已就绪" else "后台守护设置待完成", color = if (protectionReady) Primary else Color(0xFF8B5D17), fontWeight = FontWeight.Bold, fontSize = 14.sp, modifier = Modifier.padding(top = 5.dp))
        Spacer(Modifier.height(22.dp))
        AppCard {
            SectionTitle("状态", "守护信息")
            StatusLine("手机电量", if (config.lastBatteryPct > 0) "${config.lastBatteryPct}%" else "等待首次上传")
            StatusLine("待传位置", "$pending 个")
            StatusLine("最近采集", formatTime(config.lastCaptureAt, "等待首次采集"))
            if (config.lastReject.isNotBlank()) StatusLine("最近拒收", config.lastReject)
            StatusLine("最近上传", formatTime(config.lastUploadAt, "等待首次上传"))
            StatusLine("当前网络", model.networkLabel())
            StatusLine("服务最近启动", formatTime(config.lastServiceStartedAt, "等待启动"))
            StatusLine("指令通道", formatTime(config.lastCommandContactAt, "等待连接"))
            if (config.lastServiceError.isNotBlank()) StatusLine("最近异常", serviceErrorLabel(config.lastServiceError))
        }
        Spacer(Modifier.height(12.dp))
        AppCard {
            SectionTitle("后台", "后台守护设置")
            PermissionRow("始终允许定位", backgroundGranted) {
                activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}")))
            }
            PermissionRow("允许常驻通知", notificationGranted) {
                activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}")))
            }
            PermissionRow("关闭电池优化", batteryProtected) {
                activity.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
            PermissionRow("系统定位开关", locationEnabled) {
                activity.startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
            }
            Surface(color = if (protectionReady) SurfaceSubtle else WarningSoft, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth().padding(top = 10.dp)) {
                Text("锁屏后的更新情况请以家人端显示的采集时间为准。此模拟版本尚待在老人日常使用的手机上验证。", color = if (protectionReady) TextMuted else Color(0xFF8B5D17), fontSize = 13.sp, lineHeight = 19.sp, modifier = Modifier.padding(13.dp))
            }
        }
        Spacer(Modifier.height(12.dp))
        OutlinedButton(onClick = { model.unbind() }) { Text("停止位置共享") }
        Text("老人无需操作此页面", color = TextMuted, fontSize = 13.sp, modifier = Modifier.padding(top = 22.dp))
        TextButton(onClick = { manage = true }, modifier = Modifier.padding(top = 18.dp, bottom = 20.dp)) { Text("家属管理", color = TextMuted) }
    }
    if (manage) ManageDialog(model, activity) { manage = false }
}

private fun serviceErrorLabel(code: String) = when {
    code == "permission_missing" -> "缺少后台定位权限"
    code == "location_disabled" -> "系统定位已关闭"
    code == "location_timeout" -> "定位等待超时"
    code == "low_accuracy" -> "最近定位精度不足"
    code == "mock_location" -> "检测到模拟位置"
    code == "network_error" || code == "command_channel_error" -> "网络暂时不可用"
    code.startsWith("location_error_") -> "定位服务返回异常"
    else -> "定位服务需要检查"
}

private fun formatTime(value: Long, empty: String) = if (value > 0) {
    DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(value))
} else empty

@Composable
private fun StatusLine(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Top) {
        Text(label, color = TextMuted, fontSize = 13.sp)
        Text(value, fontWeight = FontWeight.SemiBold, fontSize = 13.sp, modifier = Modifier.padding(start = 16.dp))
    }
}

@Composable
private fun ManageDialog(model: MainViewModel, activity: MainActivity, dismiss: () -> Unit) {
    var pin by remember { mutableStateOf("") }
    var unlocked by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    val syncMessage by model.syncMessage.collectAsState()
    AlertDialog(
        onDismissRequest = dismiss,
        title = { Text(if (unlocked) "家属管理" else "输入管理 PIN") },
        text = {
            if (!unlocked) OutlinedTextField(
                pin,
                { pin = it.filter(Char::isDigit).take(6) },
                label = { Text("六位 PIN") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                visualTransformation = PasswordVisualTransformation(),
                isError = error.isNotEmpty(),
                supportingText = { if (error.isNotEmpty()) Text(error) },
            ) else Column {
                Text("只有家属应修改以下设置。", color = TextMuted, fontSize = 13.sp)
                OutlinedButton(onClick = { model.syncConfig() }, modifier = Modifier.fillMaxWidth().padding(top = 12.dp)) { Text("立即同步网页配置") }
                if (syncMessage != null) Text(syncMessage!!, color = if (syncMessage == "住宅配置已同步") Primary else TextMuted, fontSize = 12.sp, modifier = Modifier.padding(top = 7.dp))
                OutlinedButton(onClick = { activity.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) { Text("打开电池优化设置") }
                OutlinedButton(onClick = { activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}"))) }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) { Text("打开应用系统设置") }
                Button(onClick = { model.unbind(); dismiss() }, colors = ButtonDefaults.buttonColors(containerColor = Danger), modifier = Modifier.fillMaxWidth().padding(top = 16.dp)) { Text("解除绑定并停止定位") }
            }
        },
        confirmButton = {
            if (!unlocked) Button(onClick = {
                activity.lifecycleScope.launch {
                    if (model.verifyPin(pin)) unlocked = true else error = "PIN 不正确"
                }
            }, enabled = pin.length == 6) { Text("解锁") } else TextButton(onClick = dismiss) { Text("完成") }
        },
        dismissButton = { if (!unlocked) TextButton(onClick = dismiss) { Text("取消") } },
    )
}
