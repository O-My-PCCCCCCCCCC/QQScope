# QQScope v5 语音文字验收：构建 + 自检夹具 + 真机语音会话截图 + 断言
# 用法： powershell -ExecutionPolicy Bypass -File web\selftest\run_v5.ps1 [-Port 15555]
param([int]$Port = 15555, [int]$ProxyPort = 15599)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing

$Here = $PSScriptRoot
$Root = Split-Path -Parent (Split-Path -Parent $Here)
$Out  = Join-Path $Here 'out'
$Shots = Join-Path $Root 'docs\截图'
New-Item -ItemType Directory -Force -Path $Out, $Shots | Out-Null

$Edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$Node = 'C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
if (-not (Test-Path $Node)) { $Node = 'node' }
$Py = Join-Path $Root 'tools\nt_msg_db_util\.venv\Scripts\python.exe'
$Base = "http://127.0.0.1:$Port"
$ProxyBase = "http://127.0.0.1:$ProxyPort"

$fails = 0
function Pass($name, $cond, $detail) {
  if ($cond) { Write-Host ("  PASS  " + $name) -ForegroundColor Green }
  else { $script:fails++; Write-Host ("  FAIL  " + $name + $(if ($detail) { "  -> $detail" } else { "" })) -ForegroundColor Red }
}
function NonBlank($png) {
  try {
    $img = [System.Drawing.Image]::FromFile($png); $bmp = New-Object System.Drawing.Bitmap($img)
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    for ($y = 10; $y -lt $bmp.Height; $y += 37) { for ($x = 10; $x -lt $bmp.Width; $x += 41) { $c = $bmp.GetPixel($x, $y); [void]$set.Add("$($c.R),$($c.G),$($c.B)") } }
    $w = $bmp.Width; $h = $bmp.Height; $bmp.Dispose(); $img.Dispose(); return @{ colors = $set.Count; w = $w; h = $h }
  } catch { return @{ colors = 0; w = 0; h = 0 } }
}
function NewProfile() { $ud = Join-Path $Out ('edge-v5-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)); Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue; return $ud }
function Shot($url, $file) {
  $ud = NewProfile; Remove-Item -Force $file -ErrorAction SilentlyContinue
  Start-Process -FilePath $Edge -ArgumentList @('--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--disable-extensions', "--user-data-dir=$ud", '--window-size=1600,2000', '--virtual-time-budget=18000', "--screenshot=$file", $url) -Wait -WindowStyle Hidden | Out-Null
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  $stat = if (Test-Path $file) { NonBlank $file } else { @{ colors = 0; w = 0; h = 0 } }
  return $stat
}
function Dump($url, $file) {
  $ud = NewProfile; Remove-Item -Force $file -ErrorAction SilentlyContinue
  & $Edge --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check --disable-extensions "--user-data-dir=$ud" --window-size=1600,2000 --virtual-time-budget=18000 --dump-dom $url 2>$null | Out-File -Encoding UTF8 $file
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  return (Test-Path $file)
}

Write-Host "`n[1/5] 构建前端 + 夹具" -ForegroundColor Cyan
& $Py (Join-Path $Root 'scripts\build_web.py') 2>&1 | Select-Object -First 1
Pass "build_web.py 产物存在" (Test-Path (Join-Path $Root 'app\dist\QQScope.html'))
& $Node (Join-Path $Here 'make_media_demo.mjs')
& $Node (Join-Path $Here 'make_voice_demo.mjs')
Pass "自检夹具 + 真机导航夹具已生成" ((Test-Path (Join-Path $Out 'demo-v5-media.html')) -and (Test-Path (Join-Path $Out 'demo-v5-voice-group.html')))

Write-Host "`n[2/5] 启动只读代理（/api → 真实后端）" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*proxy_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 300
Start-Process -FilePath $Node -ArgumentList @('proxy_server.mjs', "$ProxyPort", $Base, (Join-Path $Out 'demo-v5-media.html')) -WorkingDirectory $Here -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 2
$ready = $false
for ($i = 0; $i -lt 10; $i++) { try { $r = Invoke-WebRequest "$ProxyBase/api/health" -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}; Start-Sleep -Milliseconds 400 }
Pass "代理可达" $ready

Write-Host "`n[3/5] 截图：语音文字自检 + 真机语音会话" -ForegroundColor Cyan
$stat = Shot "$ProxyBase/demo/v5-media.html?theme=dark#/sessions" (Join-Path $Shots 'v5-语音文字-自检-有文字.png')
Pass "自检截图（合成 voice_text）非空白（$($stat.colors) 色）" ($stat.colors -ge 20) "colors=$($stat.colors)"
$stat = Shot "$ProxyBase/demo/v5-voice-group.html?theme=dark#/sessions" (Join-Path $Shots 'v5-语音文字-真实群聊-未转文字.png')
$stat = Shot "$ProxyBase/demo/v5-voice-text.html?theme=dark#/sessions" (Join-Path $Shots 'v5-语音文字-真实-已转文字.png')
Pass "真机已转文字截图非空白（$($stat.colors) 色）" ($stat.colors -ge 20) "colors=$($stat.colors)"
Pass "真机群聊截图非空白（$($stat.colors) 色）" ($stat.colors -ge 20) "colors=$($stat.colors)"
$stat = Shot "$ProxyBase/demo/v5-voice-c2c.html?theme=dark#/sessions" (Join-Path $Shots 'v5-语音文字-真实私聊-未转文字.png')
Pass "真机私聊截图非空白（$($stat.colors) 色）" ($stat.colors -ge 20) "colors=$($stat.colors)"

Write-Host "`n[4/5] --dump-dom 抓取" -ForegroundColor Cyan
$domMedia = Join-Path $Out 'dom-v5-media-demo.html'
$domGroup = Join-Path $Out 'dom-v5-voice-group.html'
$domText = Join-Path $Out 'dom-v5-voice-text.html'
$domC2C = Join-Path $Out 'dom-v5-voice-c2c.html'
[void](Dump "$ProxyBase/demo/v5-media.html?theme=dark#/sessions" $domMedia)
[void](Dump "$ProxyBase/demo/v5-voice-group.html?theme=dark#/sessions" $domGroup)
[void](Dump "$ProxyBase/demo/v5-voice-text.html?theme=dark#/sessions" $domText)
[void](Dump "$ProxyBase/demo/v5-voice-c2c.html?theme=dark#/sessions" $domC2C)
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*proxy_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$domSessions = Join-Path $Out 'dom-v5-sessions-dark.html'
[void](Dump "$Base/?theme=dark#/sessions" $domSessions)

Write-Host "`n[5/5] 断言" -ForegroundColor Cyan
& $Node (Join-Path $Here 'check_v5.mjs') (Join-Path $Root 'web\index.html') $domMedia $domText $domGroup $domC2C
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v4.mjs') (Join-Path $Root 'web\index.html') $domMedia $domSessions
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v2.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Root 'app\dist\QQScope.html') $domSessions
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v3.mjs') (Join-Path $Root 'web\index.html') $domSessions
if ($LASTEXITCODE -ne 0) { $script:fails++ }

Write-Host "`n清理浏览器 profile 目录…" -ForegroundColor Cyan
Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
$left = (Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | Measure-Object).Count
Pass "web/selftest/out 无残留 profile 目录" ($left -eq 0) "残留 $left"

Write-Host "`n===== run_v5 结果：FAIL $fails =====" -ForegroundColor $(if ($fails) { 'Red' } else { 'Green' })
exit $(if ($fails) { 1 } else { 0 })