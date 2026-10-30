[CmdletBinding()]
param([string]$InstallDir,[string]$RuntimeDir,[string]$LibraryPath,[string]$QQAgentDir,[switch]$BrowseOnly,[switch]$NoLaunch,[switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = 'qq-agent-danbooru'
$version = 'v1.1.0'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA ('DanbooruToolkit\apps\' + $repo + '-' + $version) }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if (Test-Path -LiteralPath $InstallDir) {
    $marker = Join-Path $InstallDir '.danbooru-project'
    if (-not (Test-Path -LiteralPath $marker)) { throw 'Target directory already exists and is not this application. Choose another -InstallDir.' }
    if ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $repo) { throw 'Different application already occupies the target directory.' }
}
$asset = $repo + '-' + $version + '.zip'
$url = 'https://github.com/QinYooo/' + $repo + '/releases/download/' + $version + '/'
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('danbooru-download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
Write-Host ('Downloading ' + $repo + ' ' + $version + '...')
$checksums = Invoke-RestMethod -TimeoutSec 60 ($url + 'SHA256SUMS.txt')
$checksumLine = @(([string]$checksums).Split("`n") | Where-Object { $_.Trim().EndsWith($asset) })
if ($checksumLine.Count -ne 1) { throw 'Release checksum entry missing.' }
$expected = ($checksumLine[0].Trim() -split '\s+')[0]
$zip = Join-Path $temporary $asset
$systemCurl = Join-Path $env:SystemRoot 'System32\curl.exe'
if (Test-Path -LiteralPath $systemCurl) {
    & $systemCurl -4 --fail --location --silent --show-error --connect-timeout 20 --speed-time 60 --speed-limit 1024 --retry 2 --output $zip ($url + $asset)
    if ($LASTEXITCODE -ne 0) { throw 'Release download failed. Check your GitHub connection and retry.' }
} else { Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri ($url + $asset) -OutFile $zip }
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected.ToLowerInvariant()) { throw 'Release checksum mismatch.' }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $InstallDir -Force
$arguments = @{}
if ($RuntimeDir) { $arguments.RuntimeDir = $RuntimeDir }
if ($LibraryPath) { $arguments.LibraryPath = $LibraryPath }
if ($QQAgentDir) { $arguments.QQAgentDir = $QQAgentDir }
if ($BrowseOnly) { $arguments.BrowseOnly = $true }
if ($NoLaunch) { $arguments.NoLaunch = $true }
if ($NoShortcut) { $arguments.NoShortcut = $true }
& (Join-Path $InstallDir 'install.ps1') @arguments
