# QQScope v3 前端真实验收：全息 HUD 换肤 + 私聊真人头像
# 7 页 × 暗/浅 截图（1600x2000）+ --dump-dom 断言 + check_v2/check_v3
# 用法： powershell -ExecutionPolicy Bypass -File web\selftest\run_v3.ps1 [-Port 15555]
param([int]$Port = 15555)
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
if (-not (Test-Path $Edge)) { Write-Host "找不到 Edge：$Edge" -ForegroundColor Red; exit 2 }
$Node = 'C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
if (-not (Test-Path $Node)) { $Node = 'node' }
$Base = "http://127.0.0.1:$Port"

$fails = 0
function Pass($name, $cond, $detail) {
  if ($cond) { Write-Host ("  PASS  " + $name) -ForegroundColor Green }
  else { $script:fails++; Write-Host ("  FAIL  " + $name + $(if ($detail) { "  -> $detail" } else { "" })) -ForegroundColor Red }
}
function NonBlank($png) {
  try {
    $img = [System.Drawing.Image]::FromFile($png)
    $bmp = New-Object System.Drawing.Bitmap($img)
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    for ($y = 10; $y -lt $bmp.Height; $y += 37) {
      for ($x = 10; $x -lt $bmp.Width; $x += 41) {
        $c = $bmp.GetPixel($x, $y)
        [void]$set.Add("$($c.R),$($c.G),$($c.B)")
      }
    }
    $w = $bmp.Width; $h = $bmp.Height
    $bmp.Dispose(); $img.Dispose()
    return @{ colors = $set.Count; w = $w; h = $h }
  } catch { return @{ colors = 0; w = 0; h = 0 } }
}
function NewProfile() {
  $ud = Join-Path $Out ('edge-v3-' + [Guid]::NewGuid().ToString('N').Substring(0, 6))
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  return $ud
}
function Shot($url, $file) {
  $ud = NewProfile
  Remove-Item -Force $file -ErrorAction SilentlyContinue
  $p = Start-Process -FilePath $Edge -ArgumentList @('--headless=new', '--disable-gpu', '--hide-scrollbars',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions', "--user-data-dir=$ud",
    '--window-size=1600,2000', '--virtual-time-budget=14000', "--screenshot=$file", $url) -PassThru -Wait `
    -RedirectStandardError (Join-Path $Out 'edge-v3-shot.err.txt') -RedirectStandardOutput (Join-Path $Out 'edge-v3-shot.out.txt')
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  return (Test-Path $file)
}
function Dump($url, $file) {
  $ud = NewProfile
  Remove-Item -Force $file -ErrorAction SilentlyContinue
  & $Edge --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check `
    --disable-extensions "--user-data-dir=$ud" --window-size=1600,2000 --virtual-time-budget=14000 `
    --dump-dom $url 2>$null | Out-File -Encoding UTF8 $file
  Remove-Item -Recurse -Force $ud -ErrorAction SilentlyContinue
  return (Test-Path $file)
}

$pages = @(
  @{ k = 'sources';  zh = '数据源' },
  @{ k = 'overview'; zh = '总览' },
  @{ k = 'sessions'; zh = '会话' },
  @{ k = 'export';   zh = '导出' },
  @{ k = 'feeds';    zh = '动态' },
  @{ k = 'ai';       zh = 'AI解读' },
  @{ k = 'settings'; zh = '设置' }
)
$themes = @(@{ k = 'dark'; zh = '暗色' }, @{ k = 'light'; zh = '浅色' })

Write-Host "`n[1/4] 截图 7 页 × 2 主题（1600x2000）" -ForegroundColor Cyan
foreach ($th in $themes) {
  foreach ($pg in $pages) {
    $file = Join-Path $Shots ("v3-" + $th.zh + "-" + $pg.zh + ".png")
    $ok = Shot "$Base/?theme=$($th.k)#/$($pg.k)" $file
    $stat = if (Test-Path $file) { NonBlank $file } else { @{ colors = 0; w = 0; h = 0 } }
    Pass ("$($th.zh)/$($pg.zh) 截图非空白（$($stat.colors) 色 / $($stat.w)x$($stat.h)）") ($ok -and $stat.colors -ge 20) "colors=$($stat.colors)"
  }
}

Write-Host "`n[2/4] --dump-dom 抓取（暗色 7 页 + 浅色会话）" -ForegroundColor Cyan
$doms = @()
foreach ($pg in $pages) {
  $f = Join-Path $Out ("dom-v3-" + $pg.k + "-dark.html")
  [void](Dump "$Base/?theme=dark#/$($pg.k)" $f)
  $doms += $f
}
$light = Join-Path $Out 'dom-v3-sessions-light.html'
[void](Dump "$Base/?theme=light#/sessions" $light)
$doms += $light

Write-Host "`n[3/4] DOM 断言（红线）" -ForegroundColor Cyan
foreach ($f in $doms) {
  if (-not (Test-Path $f)) { Pass "dump 存在：$([IO.Path]::GetFileName($f))" $false; continue }
  $t = Get-Content -Raw -LiteralPath $f -Encoding UTF8
  $tag = [IO.Path]::GetFileName($f)
  $errAttr = [regex]::Match($t, 'data-js-errors="(\d+)"')
  $errN = if ($errAttr.Success) { [int]$errAttr.Groups[1].Value } else { 0 }
  $srcCount = ([regex]::Matches($t, '<script[^>]+src=')).Count
  $imgHttp = ([regex]::Matches($t, '<img[^>]+src="http')).Count
  $hrefHttp = ([regex]::Matches($t, 'href="http')).Count
  $hasDark = $t.Contains('#05080f'); $hasLight = $t.Contains('#f2f3f0')
  Pass "$tag 无 JS 报错" ($errN -eq 0) "data-js-errors=$errN"
  Pass "$tag 无 <script src=" ($srcCount -eq 0) "found=$srcCount"
  Pass "$tag 无 <img src=http" ($imgHttp -eq 0) "found=$imgHttp"
  Pass "$tag 无 href=http" ($hrefHttp -eq 0) "found=$hrefHttp"
  Pass "$tag 暗浅两套令牌都在" ($hasDark -and $hasLight -and $t.Contains('data-theme="light"'))
  if ($tag -like '*sessions*') {
    $groups = ([regex]::Matches($t, '<div class="conv-group" data-group=')).Count
    $avatars = ([regex]::Matches($t, '<img src="/api/avatar\?qq=')).Count
    Pass "$tag 会话三组（$groups）" ($groups -ge 3) "groups=$groups"
    Pass "$tag 三组标题" ($t.Contains('>私聊<') -and $t.Contains('>群聊<') -and $t.Contains('>其他<'))
    Pass "$tag 私聊真人头像 <img src=/api/avatar?qq= ×$avatars" ($avatars -ge 10) "avatars=$avatars"
  }
}

Write-Host "`n[4/4] 运行 check_v2 / check_v3" -ForegroundColor Cyan
& $Node (Join-Path $Here 'check_v2.mjs') (Join-Path $Root 'web\index.html') (Join-Path $Root 'app\dist\QQScope.html') @doms
if ($LASTEXITCODE -ne 0) { $script:fails++ }
& $Node (Join-Path $Here 'check_v3.mjs') (Join-Path $Root 'web\index.html') @doms
if ($LASTEXITCODE -ne 0) { $script:fails++ }

Write-Host "`n清理浏览器 profile 目录…" -ForegroundColor Cyan
Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | ForEach-Object {
  Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
}
$left = (Get-ChildItem -Path $Out -Directory -Filter 'edge-*' -ErrorAction SilentlyContinue | Measure-Object).Count
Pass "web/selftest/out 无残留 profile 目录" ($left -eq 0) "残留 $left"

Write-Host "`n===== run_v3 结果：FAIL $fails =====" -ForegroundColor $(if ($fails) { 'Red' } else { 'Green' })
exit $(if ($fails) { 1 } else { 0 })