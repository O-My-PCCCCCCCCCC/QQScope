# QQScope · 探测本机 QQ 版本与数据位置
# 用途：确认电脑上装的是新版 NTQQ 还是老版 QQ/TIM，
#       以及消息数据库文件（nt_msg.db / msg3.0.db）在哪里，
#       用于决定存量导入的解密方案。
# 只读探测，不修改任何文件。

$ErrorActionPreference = "SilentlyContinue"
$results = [System.Collections.Generic.List[string]]::new()

function Report([string]$title, [string]$detail) {
    $results.Add(("[{0}] {1}" -f $title, $detail))
}

Write-Host "===== QQScope 探测：QQ 版本与数据位置 =====" -ForegroundColor Cyan

# ---------- 1. 运行中的进程 ----------
Write-Host "`n--- 1. 运行中的 QQ 进程 ---" -ForegroundColor Yellow
$procs = Get-Process | Where-Object { $_.ProcessName -match '^(QQ|QQExternal|TIM|QQMusic)$' }
if ($procs) {
    $procs | ForEach-Object {
        $p = $_
        $path = $null
        try { $path = $p.Path } catch {}
        $ver = $null
        try { $ver = $p.VersionInfo.FileVersion } catch {}
        Report "进程" ("{0}  PID={1}  版本={2}  路径={3}" -f $p.ProcessName, $p.Id, $ver, $path)
        Write-Host ("  {0}  PID={1}  版本={2}" -f $p.ProcessName, $p.Id, $ver) -ForegroundColor Green
        if ($path) { Write-Host ("    路径: {0}" -f $path) }
    }
} else {
    Write-Host "  无 QQ 相关进程在运行（QQ 当前未打开）" -ForegroundColor DarkGray
    Report "进程" "无 QQ 进程运行"
}

# ---------- 2. 卸载注册表项（版本信息最可靠） ----------
Write-Host "`n--- 2. 注册表卸载项中的 QQ 安装信息 ---" -ForegroundColor Yellow
$uninstPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$qqApps = foreach ($p in $uninstPaths) {
    Get-ItemProperty $p 2>$null | Where-Object { $_.DisplayName -match 'QQ|腾讯|TIM' }
}
if ($qqApps) {
    $qqApps | ForEach-Object {
        Report "注册表" ("{0}  版本={1}  位置={2}" -f $_.DisplayName, $_.DisplayVersion, $_.InstallLocation)
        Write-Host ("  {0}  版本={1}" -f $_.DisplayName, $_.DisplayVersion) -ForegroundColor Green
        if ($_.InstallLocation) { Write-Host ("    位置: {0}" -f $_.InstallLocation) }
    }
} else {
    Write-Host "  注册表未找到 QQ 卸载项" -ForegroundColor DarkGray
    Report "注册表" "未找到 QQ 卸载项"
}

# ---------- 3. 常见安装目录 ----------
Write-Host "`n--- 3. 常见安装目录 ---" -ForegroundColor Yellow
$installDirs = @(
    "$env:ProgramFiles\Tencent\QQNT",              # 新版 NTQQ（系统级）
    "${env:ProgramFiles(x86)}\Tencent\QQNT",       # 新版 NTQQ（32位）
    "$env:LOCALAPPDATA\Programs\QQ",               # 新版 NTQQ（用户级安装）
    "${env:ProgramFiles(x86)}\Tencent\QQ",         # 老版 QQ
    "$env:ProgramFiles\Tencent\QQ",                # 老版 QQ（64位路径）
    "${env:ProgramFiles(x86)}\Tencent\TIM",        # TIM
    "$env:LOCALAPPDATA\Tencent\QQ"                 # 其他腾讯 QQ 数据
)
foreach ($d in $installDirs) {
    if (Test-Path $d) {
        $mainExe = Join-Path $d "QQ.exe"
        $ver = $null
        if (Test-Path $mainExe) { try { $ver = (Get-Item $mainExe).VersionInfo.FileVersion } catch {} }
        Report "安装目录" ("{0}  主程序版本={1}" -f $d, $ver)
        Write-Host ("  [存在] {0}  版本={1}" -f $d, $ver) -ForegroundColor Green
    }
}

# ---------- 4. 数据目录 ----------
Write-Host "`n--- 4. 数据目录（聊天记录所在） ---" -ForegroundColor Yellow
$dataDirs = @(
    "$env:APPDATA\Tencent\QQ",                     # 新版 NTQQ 数据根
    "$env:APPDATA\Tencent\TIM",                    # TIM 数据
    "$env:USERPROFILE\Documents\Tencent Files"     # 老版 QQ 数据（按 QQ 号分目录）
)
foreach ($d in $dataDirs) {
    if (Test-Path $d) {
        Report "数据目录" $d
        Write-Host ("  [存在] {0}" -f $d) -ForegroundColor Green
    }
}

# ---------- 5. 关键数据库文件 ----------
Write-Host "`n--- 5. 消息数据库文件（关键） ---" -ForegroundColor Yellow
$foundDb = $false

# NTQQ：nt_msg.db / nt_qq.db（SQLCipher 加密）
$ntRoot = "$env:APPDATA\Tencent\QQ"
if (Test-Path $ntRoot) {
    Get-ChildItem $ntRoot -Recurse -Depth 3 -File -Include "nt_msg.db", "nt_qq.db", "nt_group_msg.db" 2>$null |
        ForEach-Object {
            $foundDb = $true
            $mb = [math]::Round($_.Length / 1MB, 1)
            Report "NTQQ库" ("{0}  ({1} MB)  修改={2}" -f $_.FullName, $mb, $_.LastWriteTime)
            Write-Host ("  [NTQQ库] {0}  ({1} MB)" -f $_.FullName, $mb) -ForegroundColor Magenta
        }
}

# 老版：msg3.0.db 等（TEA 加密）
$oldRoot = "$env:USERPROFILE\Documents\Tencent Files"
if (Test-Path $oldRoot) {
    Get-ChildItem $oldRoot -Recurse -Depth 3 -File -Filter "msg*.db" 2>$null |
        ForEach-Object {
            $foundDb = $true
            $mb = [math]::Round($_.Length / 1MB, 1)
            Report "老版库" ("{0}  ({1} MB)  修改={2}" -f $_.FullName, $mb, $_.LastWriteTime)
            Write-Host ("  [老版库] {0}  ({1} MB)" -f $_.FullName, $mb) -ForegroundColor Magenta
        }
}

if (-not $foundDb) {
    Write-Host "  未找到消息数据库文件（可能 QQ 从未登录过，或数据在其他位置）" -ForegroundColor DarkGray
}

# ---------- 汇总 ----------
Write-Host "`n===== 汇总 =====" -ForegroundColor Cyan
$results | ForEach-Object { Write-Host "  $_" }
Write-Host "`n探测完成。"
