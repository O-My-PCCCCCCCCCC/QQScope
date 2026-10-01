# QQScope v10 · 一键复制 3D 素材（three.js + GLTFLoader + kei.vrm）
# 用法： powershell -ExecutionPolicy Bypass -File scripts\fetch_assets.ps1 [-Source "D:\用户\下载\work1\kei-showcase"]
# 说明：模型来自参考项目 kei-showcase；素材不入 git（web/assets/ 已在 .gitignore 排除）。
param(
  [string]$Source = "D:\用户\下载\work1\kei-showcase",
  [string]$Dest = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Dest) { $Dest = Join-Path $root "web\assets\vendor" }
if (-not (Test-Path $Source)) { throw "素材源不存在：$Source（可用 -Source 指定 kei-showcase 路径）" }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$items = @(
  @{ from = "vendor\three.min.js";  to = "three.min.js" },
  @{ from = "vendor\GLTFLoader.js"; to = "GLTFLoader.js" },
  @{ from = "models\kei.vrm";       to = "kei.vrm" }
)
foreach ($it in $items) {
  $s = Join-Path $Source $it.from
  $d = Join-Path $Dest $it.to
  if (-not (Test-Path $s)) { throw "缺少素材：$s" }
  Copy-Item -Force $s $d
  $mb = [Math]::Round((Get-Item $d).Length / 1MB, 2)
  Write-Host ("  {0,8} MB  {1}" -f $mb, $d)
}
Write-Host "3D 素材复制完成 → $Dest"
Write-Host "提示：kei.vrm 约 43MB，已被 .gitignore 排除，不会进仓库。"