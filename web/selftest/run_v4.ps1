# QQScope v4 媒体渲染验收：构建 + 自检夹具 + 真实后端截图 + 断言
# 用法： powershell -ExecutionPolicy Bypass -File web\selftest\run_v4.ps1 [-Port 15555]
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
function NewProfile() { $ud = Join-Path $Out ('edge-v4-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)); Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue; return $ud }
function Shot($url, $file) {
  $ud = NewProfile; Remove-Item -Force $file -ErrorAction SilentlyContinue
  Start-Process -FilePath $Edge -ArgumentList @('--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--disable-extensions', "--user-data-dir=$ud", '--window-size=1600,2000', '--virtual-time-budget=16000', "--screenshot=$file", $url) -Wait -WindowStyle Hidden | Out-Null
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  $stat = if (Test-Path $file) { NonBlank $file } else { @{ colors = 0; w = 0; h = 0 } }
  return $stat
}
function Dump($url, $file) {
  $ud = NewProfile; Remove-Item -Force $file -ErrorAction SilentlyContinue
  & $Edge --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check --disable-extensions "--user-data-dir=$ud" --window-size=1600,2000 --virtual-time-budget=16000 --dump-dom $url 2>$null | Out-File -Encoding UTF8 $file
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  return (Test-Path $file)
}

Write-Host "`n[1/5] 构建前端" -ForegroundColor Cyan
& $Py (Join-Path $Root 'scripts\build_web.py') 2>&1 | Select-Object -First 1
Pass "build_web.py 产物存在" (Test-Path (Join-Path $Root 'app\dist\QQScope.html'))

Write-Host "`n[2/5] 生成媒体自检夹具" -ForegroundColor Cyan
& $Node (Join-Path $Here 'make_media_demo.mjs')
Pass "自检夹具已生成" (Test-Path (Join-Path $Out 'demo-v4-media.html'))

Write-Host "`n[3/5] 真实后端截图（media 列尚未回填 → 优雅降级）" -ForegroundColor Cyan
foreach ($th in @(@{k='dark';zh='暗色'}, @{k='light';zh='浅色'})) {
  $file = Join-Path $Shots ("v4-媒体-真实会话-media待回填-" + $th.zh + ".png")
  $stat = Shot "$Base/?theme=$($th.k)#/sessions" $file
  Pass "真实后端 $($th.zh)/会话 截图非空白（$($stat.colors) 色）" ($stat.colors -ge 20) "colors=$($stat.colors)"
}
$stat = Shot "$Base/?theme=dark#/overview" (Join-Path $Shots 'v4-媒体-总览-命中率卡待就绪.png')
Pass "真实后端 暗色/总览 截图非空白（$($stat.colors) 色）" ($stat.colors -ge 20) "colors=$($stat.colors)"

Write-Host "`n[4/5] 媒体渲染自检（合成元数据，/api/media 一律 404 → 占位徽章）" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*proxy_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 300
Start-Process -FilePath $Node -ArgumentList @('proxy_server.mjs', "$ProxyPort", $Base, (Join-Path $Out 'demo-v4-media.html')) -WorkingDirectory $Here -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 2
$ready = $false
for ($i = 0; $i -lt 10; $i++) { try { $r = Invoke-WebRequest "$ProxyBase/api/health" -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}; Start-Sleep -Milliseconds 400 }
Pass "自检代理可达" $ready
$domMedia = Join-Path $Out 'dom-v4-media-demo.html'
[void](Dump "$ProxyBase/demo/v4-media.html?theme=dark#/sessions" $domMedia)
$stat = Shot "$ProxyBase/demo/v4-media.html?theme=dark#/sessions" (Join-Path $Shots 'v4-媒体-占位徽章-自检.png')
Pass "媒体自检截图非空白（$($stat.colors) 色）" ($stat.colors -ge 20) "colors=$($stat.colors)"
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*proxy_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "`n[5/5] DOM 抓取 + 断言" -ForegroundColor Cyan
$domDark = Join-Path $Out 'dom-v4-sessions-dark.html'
$domLight = Join-Path $Out 'dom-v4-sessions-light.html'
[void](Dump "$Base/?theme=dark#/sessions" $domDark)
[void](Dump "$Base/?theme=light#/sessions" $domLight)
$md = Get-Content -Raw -LiteralPath $domMedia -Encoding UTF8
Pass "自检 DOM 含 <img src=/api/media/" (([regex]::Matches($md, '<img class="media-el[^"]*"[^>]*src="/api/media/')).Count -ge 3)
Pass "自检 DOM 含 <audio src=/api/media/" (([regex]::Matches($md, '<audio[^>]*src="/api/media/')).Count -ge 2)
Pass "自检 DOM 占位徽章" (([regex]::Matches($md, 'class="media-badge"')).Count -ge 6)
& $Node (Join-Path $Here 'check_v4.mjs') (Join-Path $Root 'web\index.html') $domMedia $domDark $domLight
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v2.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Root 'app\dist\QQScope.html') $domDark $domLight
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v3.mjs') (Join-Path $Root 'web\index.html') $domDark $domLight
if ($LASTEXITCODE -ne 0) { $script:fails++ }

Write-Host "`n清理浏览器 profile 目录…" -ForegroundColor Cyan
Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
$left = (Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | Measure-Object).Count
Pass "web/selftest/out 无残留 profile 目录" ($left -eq 0) "残留 $left"

Write-Host "`n===== run_v4 结果：FAIL $fails =====" -ForegroundColor $(if ($fails) { 'Red' } else { 'Green' })
exit $(if ($fails) { 1 } else { 0 })