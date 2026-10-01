# QQScope v6 验收：名片卡片 + dossier 不自动打开 + 媒体性能
# 用法： powershell -ExecutionPolicy Bypass -File web\selftest\run_v6.ps1 [-Port 15555]
param([int]$Port = 15555, [int]$ProxyPort = 15599)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing

$Here = $PSScriptRoot
$Root = Split-Path -Parent (Split-Path -Parent $Here)
$Out = Join-Path $Here 'out'
$Shots = Join-Path $Root 'docs\截图'
New-Item -ItemType Directory -Force -Path $Out, $Shots | Out-Null
$Edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$Node = 'C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
if (-not (Test-Path $Node)) { $Node = 'node' }
$Py = Join-Path $Root 'tools\nt_msg_db_util\.venv\Scripts\python.exe'
$Base = "http://127.0.0.1:$Port"
$ProxyBase = "http://127.0.0.1:$ProxyPort"
$fails = 0
function Pass($n, $c, $d) { if ($c) { Write-Host ("  PASS  " + $n) -ForegroundColor Green } else { $script:fails++; Write-Host ("  FAIL  " + $n + $(if ($d) { "  -> $d" } else { "" })) -ForegroundColor Red } }
function NonBlank($png) {
  try { $img = [System.Drawing.Image]::FromFile($png); $bmp = New-Object System.Drawing.Bitmap($img); $set = New-Object 'System.Collections.Generic.HashSet[string]'
    for ($y = 10; $y -lt $bmp.Height; $y += 37) { for ($x = 10; $x -lt $bmp.Width; $x += 41) { $cc = $bmp.GetPixel($x, $y); [void]$set.Add("$($cc.R),$($cc.G),$($cc.B)") } }
    $w = $bmp.Width; $h = $bmp.Height; $bmp.Dispose(); $img.Dispose(); return @{ colors = $set.Count; w = $w; h = $h } } catch { return @{ colors = 0; w = 0; h = 0 } }
}
function NewProfile() { $ud = Join-Path $Out ('edge-v6-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)); Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue; return $ud }
function Dump($url, $file, $budget) { $ud = NewProfile; Remove-Item -Force $file -ErrorAction SilentlyContinue; & $Edge --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check --disable-extensions "--user-data-dir=$ud" --window-size=1600,2000 --virtual-time-budget=$budget --dump-dom $url 2>$null | Out-File -Encoding UTF8 $file; Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue; return (Test-Path $file) }
function Shot($url, $file, $budget) { $ud = NewProfile; $tmp = [string](Join-Path $Out ('shot-tmp-' + [Guid]::NewGuid().ToString('N').Substring(0,6) + '.png')); $dst = [string]$file; Remove-Item -Force -LiteralPath $tmp -ErrorAction SilentlyContinue; Remove-Item -Force -LiteralPath $dst -ErrorAction SilentlyContinue; Start-Process -FilePath $Edge -ArgumentList @('--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--disable-extensions', "--user-data-dir=$ud", '--window-size=1600,2000', "--virtual-time-budget=$budget", "--screenshot=$tmp", $url) -Wait -WindowStyle Hidden | Out-Null; Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath $tmp) { Move-Item -Force -LiteralPath $tmp -Destination $dst }; $s = if (Test-Path -LiteralPath $dst) { NonBlank $dst } else { @{ colors = 0 } }; return $s }

Write-Host "`n[1/5] 构建 + 夹具" -ForegroundColor Cyan
& $Py (Join-Path $Root 'scripts\build_web.py') 2>&1 | Select-Object -First 1
& $Node (Join-Path $Here 'make_media_demo.mjs')
& $Node (Join-Path $Here 'make_voice_demo.mjs')
& $Node (Join-Path $Here 'make_v6_demo.mjs')
& $Node (Join-Path $Here 'make_perf_demo.mjs') '蔚蓝档案交流群'
$perf = Join-Path $Out 'demo-v6-perf.html'
$perfHtml = Get-Content -Raw -LiteralPath $perf -Encoding UTF8
[IO.File]::WriteAllText((Join-Path $Out 'demo-v6-perf-before.html'), $perfHtml.Replace('data-media-src="'' + url + ''"', 'src="'' + url + ''"'), (New-Object System.Text.UTF8Encoding($false)))
[IO.File]::WriteAllText((Join-Path $Out 'demo-v6-perf-pagebefore.html'), $perfHtml.Replace('var r2 = buildBubbles(items, state._topDay || "");', 'var r2 = buildBubbles(state.messages, "");').Replace('box.insertAdjacentHTML("afterbegin", r2.html);', 'box.innerHTML = r2.html;'), (New-Object System.Text.UTF8Encoding($false)))
Pass "夹具就绪" ((Test-Path (Join-Path $Out 'demo-v6-card.html')) -and (Test-Path $perf))

Write-Host "`n[2/5] 启动只读代理" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*proxy_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 300
Start-Process -FilePath $Node -ArgumentList @('proxy_server.mjs', "$ProxyPort", $Base, (Join-Path $Out 'demo-v6-card.html')) -WorkingDirectory $Here -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 2
$ready = $false
for ($i = 0; $i -lt 10; $i++) { try { $r = Invoke-WebRequest "$ProxyBase/api/health" -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}; Start-Sleep -Milliseconds 400 }
Pass "代理可达" $ready

Write-Host "`n[3/5] 截图" -ForegroundColor Cyan
$shotDefs = @(
  @{ u = "$ProxyBase/demo/v6-card.html?theme=dark#/sessions";     f = 'v6-名片卡片.png' },
  @{ u = "$ProxyBase/demo/v6-cardmini.html?theme=dark#/sessions"; f = 'v6-小程序包名美化.png' },
  @{ u = "$ProxyBase/demo/v6-avatar.html?theme=dark#/sessions";   f = 'v6-资料卡不自动打开-点头像.png' },
  @{ u = "$ProxyBase/demo/v6-row.html?theme=dark#/sessions";      f = 'v6-资料卡不自动打开-点会话行.png' },
  @{ u = "$ProxyBase/demo/v6-open.html?theme=dark#/sessions";     f = 'v6-资料卡不自动打开-点资料卡按钮.png' },
  @{ u = "$ProxyBase/demo/v6-switch.html?theme=dark#/sessions";   f = 'v6-资料卡不自动打开-切换后关闭.png' },
  @{ u = "$Base/?theme=dark#/overview";                            f = 'v6-语音进度.png' }
)
foreach ($s in $shotDefs) { $st = Shot $s.u (Join-Path $Shots $s.f) 18000; Pass "$($s.f) 非空白（$($st.colors) 色）" ($st.colors -ge 20) "colors=$($st.colors)" }

Write-Host "`n[4/5] DOM 抓取" -ForegroundColor Cyan
Dump "$ProxyBase/demo/v6-card.html?theme=dark#/sessions" (Join-Path $Out 'dom-v6-card.html') 18000
Dump "$ProxyBase/demo/v6-cardmini.html?theme=dark#/sessions" (Join-Path $Out 'dom-v6-cardmini.html') 18000
Dump "$ProxyBase/demo/v6-avatar.html?theme=dark#/sessions" (Join-Path $Out 'dom-v6-avatar.html') 16000
Dump "$ProxyBase/demo/v6-row.html?theme=dark#/sessions" (Join-Path $Out 'dom-v6-row.html') 16000
Dump "$ProxyBase/demo/v6-open.html?theme=dark#/sessions" (Join-Path $Out 'dom-v6-open.html') 16000
Dump "$ProxyBase/demo/v6-switch.html?theme=dark#/sessions" (Join-Path $Out 'dom-v6-switch.html') 18000
Dump "$ProxyBase/demo/v5-media.html?theme=dark#/sessions" (Join-Path $Out 'dom-v4-media-demo.html') 18000
Dump "$ProxyBase/demo/v5-voice-text.html?theme=dark#/sessions" (Join-Path $Out 'dom-v5-voice-text.html') 18000
Dump "$ProxyBase/demo/v5-voice-group.html?theme=dark#/sessions" (Join-Path $Out 'dom-v5-voice-group.html') 18000
Dump "$ProxyBase/demo/v5-voice-c2c.html?theme=dark#/sessions" (Join-Path $Out 'dom-v5-voice-c2c.html') 18000
Dump "$ProxyBase/demo/v6-perf.html?theme=dark#/sessions" (Join-Path $Out 'dom-perf-after.html') 45000
Dump "$ProxyBase/demo/v6-perf-before.html?theme=dark#/sessions" (Join-Path $Out 'dom-perf-before2.html') 45000
Dump "$ProxyBase/demo/v6-perf-pagebefore.html?theme=dark#/sessions" (Join-Path $Out 'dom-perf-pagebefore.html') 45000
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*proxy_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Dump "$Base/?theme=dark#/sessions" (Join-Path $Out 'dom-v6-sessions-dark.html') 16000
Dump "$Base/?theme=light#/sessions" (Join-Path $Out 'dom-v6-sessions-light.html') 16000
Dump "$Base/?theme=dark#/overview" (Join-Path $Out 'dom-v6-overview-dark.html') 16000

Write-Host "`n[5/5] 断言" -ForegroundColor Cyan
& $Node (Join-Path $Here 'check_v6.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Out 'dom-v6-card.html') (Join-Path $Out 'dom-v6-cardmini.html') (Join-Path $Out 'dom-v6-avatar.html') (Join-Path $Out 'dom-v6-row.html') (Join-Path $Out 'dom-v6-open.html') (Join-Path $Out 'dom-v6-switch.html') (Join-Path $Out 'dom-perf-after.html')
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v5.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Out 'dom-v4-media-demo.html') (Join-Path $Out 'dom-v5-voice-text.html') (Join-Path $Out 'dom-v5-voice-group.html') (Join-Path $Out 'dom-v5-voice-c2c.html')
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v4.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Out 'dom-v4-media-demo.html') (Join-Path $Out 'dom-v6-sessions-dark.html')
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v2.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Root 'app\dist\QQScope.html') (Join-Path $Out 'dom-v6-sessions-dark.html') (Join-Path $Out 'dom-v6-sessions-light.html')
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v3.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Out 'dom-v6-sessions-dark.html') (Join-Path $Out 'dom-v6-sessions-light.html')
if ($LASTEXITCODE -ne 0) { $script:fails++ }

Write-Host "`n清理浏览器 profile 目录…" -ForegroundColor Cyan
Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
$left = (Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | Measure-Object).Count
Pass "web/selftest/out 无残留 profile 目录" ($left -eq 0) "残留 $left"
Write-Host "`n===== run_v6 结果：FAIL $fails =====" -ForegroundColor $(if ($fails) { 'Red' } else { 'Green' })
exit $(if ($fails) { 1 } else { 0 })