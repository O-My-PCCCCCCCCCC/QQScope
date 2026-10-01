# QQScope 前端一键验收（零依赖 mock 后端 + 无头 Edge）
# 用法： powershell -ExecutionPolicy Bypass -File web\selftest\run_edge.ps1 [-Port 15556]
param([int]$Port = 15556)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing

$Here = $PSScriptRoot
$Root = Split-Path -Parent (Split-Path -Parent $Here)
$Out  = Join-Path $Here 'out'
New-Item -ItemType Directory -Force -Path $Out | Out-Null

$Node = 'C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
if (-not (Test-Path $Node)) { $Node = 'node' }
$Edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
if (-not (Test-Path $Edge)) { Write-Host "找不到 Edge：$Edge" -ForegroundColor Red; exit 2 }

$fails = 0
function Pass($name, $cond, $detail) {
  if ($cond) { Write-Host ("  PASS  " + $name) -ForegroundColor Green }
  else { $script:fails++; Write-Host ("  FAIL  " + $name + $(if ($detail) { "  -> $detail" } else { "" })) -ForegroundColor Red }
}

Write-Host "`n[1/5] 构建自测产物 + 静态断言" -ForegroundColor Cyan
& $Node (Join-Path $Here 'build.mjs')
if ($LASTEXITCODE -ne 0) { Write-Host 'build.mjs 失败' -ForegroundColor Red; exit 1 }
& $Node (Join-Path $Here 'check_web.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Out 'QQScope.selftest.html') (Join-Path $Out 'QQScope.api.html')
if ($LASTEXITCODE -ne 0) { $fails++ }

Write-Host "`n[2/5] 生成 demo 页（自动触发按钮，供截图）" -ForegroundColor Cyan
$api = [IO.File]::ReadAllText((Join-Path $Out 'QQScope.api.html'))
function New-Demo($name, $hash, $js) {
  $body = "location.hash='#$hash';setTimeout(function(){$js},2600);"
  $html = $api.Replace('</body>', "<script>`n$body`n</script>`n</body>")
  [IO.File]::WriteAllText((Join-Path $Out "demo-$name.html"), $html, (New-Object System.Text.UTF8Encoding($false)))
}
New-Demo 'sources'  'sources'  "var bb=document.getElementById('botBase');if(bb)bb.value='127.0.0.1:15556';var a=document.getElementById('btnPackScan');if(a)a.click();var b=document.getElementById('btnBotProbe');if(b)b.click();"
New-Demo 'sessions' 'sessions' "var it=document.querySelectorAll('.conv-item');if(it.length)it[0].click();"
New-Demo 'export'   'export'   "var a=document.getElementById('expAll');if(a)a.click();"
New-Demo 'overview' 'overview' "window.scrollTo(0,0);"
New-Demo 'ai'       'ai'       "var a=document.getElementById('btnAiSend');if(a)a.click();"
New-Demo 'botfail'  'sources'  "(function(){var rf=window.fetch;window.fetch=function(u,o){if(String(u).indexOf('/api/sources/bot/probe')===0){return Promise.reject(new Error('目标计算机拒绝连接'));}return rf(u,o);};})();var b=document.getElementById('botBase');if(b)b.value='127.0.0.1:3999';var a=document.getElementById('btnBotProbe');if(a)a.click();"

Write-Host "`n[3/5] 启动 mock 后端 :$Port" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*mock_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400
Start-Process -FilePath $Node -ArgumentList 'mock_server.mjs', "$Port" -WorkingDirectory $Here -RedirectStandardOutput (Join-Path $Out 'mock.log') -RedirectStandardError (Join-Path $Out 'mock.err.log') -WindowStyle Hidden | Out-Null
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 400
  try { $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}
}
Pass "mock /api/health 可达" $ready
if (-not $ready) { exit 1 }
$Base = "http://127.0.0.1:$Port"

function Dump-Dom($name, $url) {
  $ud = Join-Path $Out ("edge-d-" + $name + "-" + [Guid]::NewGuid().ToString('N').Substring(0,6))
  $file = Join-Path $Out ("dom-demo-" + $name + ".html")
  & $Edge --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check --disable-extensions "--user-data-dir=$ud" --window-size=1440,2000 --virtual-time-budget=12000 --dump-dom $url 2>$null | Out-File -Encoding UTF8 $file
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  return (Get-Content -Raw -Encoding UTF8 $file)
}
function Rendered($html) { $cut = $html.LastIndexOf('<script>'); if ($cut -lt 0) { $cut = $html.Length }; return $html.Substring(0, $cut) }
function Plain($html) { $t = [regex]::Replace($html, '<[^>]+>', ' '); return [regex]::Replace($t, '\s+', ' ') }

Write-Host "`n[4/5] 无头 Edge 渲染 + JS 报错检查" -ForegroundColor Cyan
$cases = @(
  @{ n='sources';  u="$Base/demo/sources.html";   must=@('窗口 A · 数据包读取','窗口 B · 机器人框架读取','发现 2 个账号','密钥 ✓','缺密钥','MockBot','好友数','可采集会话') },
  @{ n='sessions'; u="$Base/demo/sessions.html";  must=@('同桌','李四','摸鱼群','加载更早（剩余','今天有点累','私聊 2','群聊 1') },
  @{ n='export';   u="$Base/demo/export.html";    must=@('已选 3 个','同桌','李四','摸鱼群') },
  @{ n='overview'; u="$Base/demo/overview.html";  must=@('总消息','自己发的','私聊','群聊','联系人','时间跨度','情绪曲线','活跃时段','每日明细') },
  @{ n='ai';       u="$Base/demo/ai.html";        must=@('将要发送的内容预览','【Mock AI】','确认并发送给 AI') },
  @{ n='botfail';  u="$Base/demo/botfail.html";   must=@('连接失败','目标计算机拒绝连接') }
)
foreach ($c in $cases) {
  $html = Dump-Dom $c.n $c.u
  $render = Rendered $html
  $errAttr = if ($html -match 'data-js-errors="(\d+)"') { [int]$Matches[1] } else { 0 }
  $plain = Plain $render
  $missing = @($c.must | Where-Object { $render.IndexOf($_) -lt 0 -and $plain.IndexOf($_) -lt 0 })
  Pass ("$($c.n)：无 JS 报错") ($errAttr -eq 0) "data-js-errors=$errAttr"
  Pass ("$($c.n)：关键内容渲染（$($c.must.Count) 项）") ($missing.Count -eq 0) ("缺失: " + ($missing -join ', '))
}

Write-Host "`n[5/5] 截图（1440x2000）" -ForegroundColor Cyan
function Shot($name, $url) {
  $ud = Join-Path $Out ("edge-p-" + $name + "-" + [Guid]::NewGuid().ToString('N').Substring(0,6))
  $png = Join-Path $Out ("shot-" + $name + ".png")
  Remove-Item -Force $png -ErrorAction SilentlyContinue
  & $Edge --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check --disable-extensions "--user-data-dir=$ud" --window-size=1440,2000 --virtual-time-budget=12000 "--screenshot=$png" $url 2>$null | Out-Null
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  if (-not (Test-Path $png)) { return $null }
  $bmp = [System.Drawing.Bitmap]::FromFile($png)
  $total = 0; $white = 0
  for ($y = 0; $y -lt $bmp.Height; $y += 25) { for ($x = 0; $x -lt $bmp.Width; $x += 25) { $p = $bmp.GetPixel($x, $y); $total++; if ($p.R -gt 246 -and $p.G -gt 246 -and $p.B -gt 246) { $white++ } } }
  $ratio = 100 * $white / [Math]::Max(1, $total)
  $size = (Get-Item $png).Length
  $bmp.Dispose()
  return @{ png=$png; size=$size; white=$ratio }
}
$shots = @(
  @{ n='00-raw-index-sources'; u="file:///" + ((Join-Path $Root 'web\index.html') -replace '\\','/') },
  @{ n='01-sources';  u="$Base/demo/sources.html" },
  @{ n='02-sessions'; u="$Base/demo/sessions.html" },
  @{ n='03-export';   u="$Base/demo/export.html" },
  @{ n='04-overview'; u="$Base/demo/overview.html" },
  @{ n='05-ai';       u="$Base/demo/ai.html" },
  @{ n='06-settings'; u="$Base/#settings" },
  @{ n='07-offline-snapshot'; u="file:///" + ((Join-Path $Out 'QQScope.selftest.html') -replace '\\','/') + "#sessions" },
  @{ n='08-bot-error'; u="$Base/demo/botfail.html" }
)
foreach ($s in $shots) {
  $r = Shot $s.n $s.u
  if ($r) { Pass ("截图 " + $s.n) (($r.size -gt 20000) -and ($r.white -lt 95)) ("size=" + $r.size + " white=" + [Math]::Round($r.white,1) + "% -> " + $r.png) }
  else { Pass ("截图 " + $s.n) $false '未生成' }
}

Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*mock_server.mjs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host ("`n===== 前端验收：" + $(if ($fails -eq 0) { '全部通过 ✅' } else { "$fails 项失败 ❌" }) + " =====") -ForegroundColor $(if ($fails -eq 0) { 'Green' } else { 'Red' })
exit $(if ($fails -eq 0) { 0 } else { 1 })