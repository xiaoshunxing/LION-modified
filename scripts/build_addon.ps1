<#
.SYNOPSIS
    打包 LION 修改版为可安装的 .nvda-addon，并做包体检。

.DESCRIPTION
    1. 体检源目录：必须存在 manifest.ini；不得出现 PaddleOCR/、PIL/、__pycache__、*.pyc、*.log
    2. 重新生成 doc/<lang>/readme.html（调用 scripts/make_readme_html.py）
    3. 打包成 dist/<name>-<version>.nvda-addon（zip 容器，UTF-8 文件名）
    4. 解压产物复检：条目清单、必填文件、禁止项、体积、SHA256

    变体：
      -Variant full（默认）完整版，源目录 src/
      -Variant lite        简洁版，源目录 variants/lite/src/，
                           打包前把完整版的 ocrClient.py 复制进临时 staging（客户端单一真源）

.NOTES
    只写 dist/ 与 tests/artifacts/；不安装插件、不改动 NVDA 配置。
    产物约定：只覆盖同名包，不删除 dist/ 里的其他文件。
    Python 默认从 LION_PYTHON 或 PATH 里的 python 解析，也可用 -Python 指定。
#>
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
[CmdletBinding()]
param(
	[switch]$SkipHtml,
	[ValidateSet('full', 'lite')]
	[string]$Variant = 'full',
	[string]$Python = $(if ($env:LION_PYTHON) { $env:LION_PYTHON } else { 'python' }),
	[string]$Configuration = 'Release'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist'
$artifacts = Join-Path $root 'tests\artifacts'
$stage = $null
if ($Variant -eq 'lite') {
	# lite 版的 ocrClient.py 不存源码副本：打包前从完整版复制到临时 staging，
	# 保证两个插件的客户端始终一致（单一真源）。
	$stage = Join-Path $root 'build\lite'
	if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
	New-Item -ItemType Directory -Force -Path (Join-Path $stage 'globalPlugins\lionlite') | Out-Null
	Copy-Item -Path (Join-Path $root 'variants\lite\src\*') -Destination $stage -Recurse -Force
	Copy-Item -Path (Join-Path $root 'src\globalPlugins\lion\ocrClient.py') `
		-Destination (Join-Path $stage 'globalPlugins\lionlite\ocrClient.py') -Force
	$src = $stage
} else {
	$src = Join-Path $root 'src'
}
$manifestPath = Join-Path $src 'manifest.ini'

function Fail([string]$message) {
	Write-Host "FAIL: $message" -ForegroundColor Red
	exit 1
}

function Pass([string]$message) {
	Write-Host "  ok  $message" -ForegroundColor Green
}

Write-Host ("== 1/5 源目录体检（变体：{0}）==" -f $Variant) -ForegroundColor Cyan
if (-not (Test-Path $manifestPath)) { Fail "找不到 $manifestPath" }

$forbidden = @()
foreach ($name in 'PaddleOCR', 'PIL') {
	$path = Join-Path $src $name
	if (Test-Path $path) { $forbidden += $path }
}
$forbidden += Get-ChildItem -Path $src -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue
$forbidden += Get-ChildItem -Path $src -Recurse -File -Include '*.pyc', '*.pyo', '*.log' -ErrorAction SilentlyContinue
if ($forbidden.Count -gt 0) {
	Fail ("src/ 里存在不应打包的内容：`n" + (($forbidden | ForEach-Object { "    $_" }) -join "`n"))
}
Pass '没有内置引擎 / 捆绑 PIL / 字节码 / 日志残留'

# 用 manifest.ini 里的 name/version 决定产物名
$manifestText = Get-Content -Raw -Encoding UTF8 $manifestPath
$nameMatch = [regex]::Match($manifestText, '(?m)^name\s*=\s*(.+?)\s*$')
$versionMatch = [regex]::Match($manifestText, '(?m)^version\s*=\s*(.+?)\s*$')
if (-not $nameMatch.Success -or -not $versionMatch.Success) { Fail 'manifest.ini 缺少 name 或 version' }
$addonName = $nameMatch.Groups[1].Value.Trim()
$addonVersion = $versionMatch.Groups[1].Value.Trim()
if ($addonName -eq 'LION') { Fail 'addon 名称不能是 LION，会与原版冲突' }
Pass "addon 标识 $addonName，版本 $addonVersion"

$pythonExe = (Get-Command $Python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $pythonExe) { Fail "找不到 Python：$Python。请用 -Python 或 LION_PYTHON 指定。" }

Write-Host "== 2/5 重新生成 readme.html ==" -ForegroundColor Cyan
if (-not $SkipHtml) {
	& $pythonExe (Join-Path $PSScriptRoot 'make_readme_html.py') --src $src
	if ($LASTEXITCODE -ne 0) { Fail '生成 readme.html 失败' }
}
foreach ($lang in 'zh_CN', 'en') {
	$html = Join-Path $src "doc\$lang\readme.html"
	if (-not (Test-Path $html)) { Fail "缺少文档 $html" }
}
Pass '中英文 readme.html 就绪'

Write-Host "== 3/5 逐文件语法与版本一致性 ==" -ForegroundColor Cyan
$pyFiles = Get-ChildItem -Path $src -Recurse -File -Filter '*.py'
$check = & $pythonExe -c @"
import ast, sys
bad = []
for path in sys.argv[1:]:
    try:
        ast.parse(open(path, encoding='utf-8').read(), filename=path)
    except SyntaxError as exc:
        bad.append(f'{path}: {exc}')
print('OK' if not bad else '\n'.join(bad))
"@ ($pyFiles | ForEach-Object { $_.FullName })
if ($check -ne 'OK') { Fail "Python 语法检查失败：`n$check" }
Pass "$($pyFiles.Count) 个 .py 文件语法正确"

$packageDir = Get-ChildItem -Path (Join-Path $src 'globalPlugins') -Directory | Select-Object -First 1
if (-not $packageDir) { Fail '找不到 globalPlugins 下的插件包目录' }
$packageEntry = 'globalPlugins/' + $packageDir.Name
$initPath = Join-Path $packageDir.FullName '__init__.py'
if (-not (Test-Path $initPath)) { Fail "找不到 $initPath" }
$initText = Get-Content -Raw -Encoding UTF8 $initPath
if ($initText -notmatch 'config\.conf\.spec\[CONFIG_SECTION\]' -or $initText -notmatch 'CONFIG_SECTION\s*=\s*"LION-modified') {
	Fail '__init__.py 没有使用独立的配置节 LION-modified'
}
if ($initText -match 'PaddleOCR-json' -or $initText -match 'GetOcrApi') { Fail '仍残留内置引擎调用' }
Pass '配置节独立，且不含内置引擎残留'

Write-Host "== 4/5 打包 ==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $dist, $artifacts | Out-Null
$bundlePath = Join-Path $dist "$addonName-$addonVersion.nvda-addon"
if (Test-Path $bundlePath) { Remove-Item $bundlePath -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($bundlePath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
	foreach ($file in Get-ChildItem -Path $src -Recurse -File) {
		$relative = $file.FullName.Substring($src.Length + 1).Replace('\', '/')
		[void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
			$zip, $file.FullName, $relative, [System.IO.Compression.CompressionLevel]::Optimal
		)
	}
}
finally {
	$zip.Dispose()
}
Pass ("已生成 {0}（变体 {1}）" -f $bundlePath, $Variant)

Write-Host "== 5/5 产物复检 ==" -ForegroundColor Cyan
$archive = [System.IO.Compression.ZipFile]::OpenRead($bundlePath)
try {
	$entries = $archive.Entries | ForEach-Object { $_.FullName }
}
finally {
	$archive.Dispose()
}

$problems = @()
foreach ($entry in $entries) {
	if ($entry -match '(^|/)(__pycache__|PaddleOCR|PIL)(/|$)' -or $entry -match '\.(pyc|pyo|log)$') {
		$problems += "禁止条目：$entry"
	}
}
foreach ($required in 'manifest.ini', "$packageEntry/__init__.py", "$packageEntry/ocrClient.py",
	"$packageEntry/lionGui.py", 'doc/zh_CN/readme.html', 'doc/en/readme.html',
	'locale/zh_CN/LC_MESSAGES/nvda.mo', 'locale/zh_CN/LC_MESSAGES/nvda.po') {
	if ($entries -notcontains $required) { $problems += "缺少必需条目：$required" }
}
if ($entries -contains 'PaddleOCR/PaddleOCR-json.exe') { $problems += '仍然打包了内置引擎' }
if ($problems.Count -gt 0) { Fail (($problems | ForEach-Object { "    $_" }) -join "`n") }
Pass "共 $($entries.Count) 个条目，必填项齐全，无禁止项"

$size = (Get-Item $bundlePath).Length
if ($size -gt 2MB) { Fail ("产物过大：{0:N2} MB（预期 < 2 MB）" -f ($size / 1MB)) }
Pass ("体积 {0:N1} KB" -f ($size / 1KB))

$hash = (Get-FileHash -Algorithm SHA256 $bundlePath).Hash
$listingPath = Join-Path $dist "$addonName-$addonVersion.files.txt"
@(
	"# $addonName $addonVersion",
	"# sha256: $hash",
	"# size: $size bytes",
	"# generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
) + ($entries | Sort-Object) | Set-Content -Encoding UTF8 $listingPath

$reportName = if ($Variant -eq 'lite') { 'build-report-lite.json' } else { 'build-report.json' }
$reportPath = Join-Path $artifacts $reportName
[ordered]@{
	name           = $addonName
	version        = $addonVersion
	bundle         = $bundlePath
	sha256         = $hash
	bytes          = $size
	entries        = $entries.Count
	variant        = $Variant
	configuration  = $Configuration
	generatedAt    = (Get-Date).ToString('o')
} | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $reportPath

Write-Host ""
if ($stage -and (Test-Path $stage)) { Remove-Item -Recurse -Force $stage }

Write-Host "SHA256 : $hash"
Write-Host "清单   : $listingPath"
Write-Host "报告   : $reportPath"
Write-Host "完成（未安装任何东西，安装请自行通过 NVDA 的“插件与更新”进行）" -ForegroundColor Green
