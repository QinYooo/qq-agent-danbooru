[CmdletBinding()]
param(
    [string]$RuntimeDir = (Join-Path $env:LOCALAPPDATA 'DanbooruToolkit'),
    [string]$LibraryPath = (Join-Path ([Environment]::GetFolderPath('MyPictures')) 'Danbooru'),
    [string]$QQAgentDir = (Join-Path $env:LOCALAPPDATA 'Programs\QQ Agent'),
    [switch]$BrowseOnly,
    [switch]$NoLaunch,
    [switch]$NoShortcut,
    [switch]$SkipDownloads,
    [string]$ExistingPython
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$project = $PSScriptRoot
if (-not $project) { throw 'Download the release ZIP and run install.ps1 from its extracted directory.' }
if (-not $LibraryPath) { $LibraryPath = Join-Path $env:USERPROFILE 'Pictures\Danbooru' }
$RuntimeDir = [IO.Path]::GetFullPath($RuntimeDir)
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
if ($ExistingPython) {
    $pythonExe = (Resolve-Path -LiteralPath $ExistingPython).Path
} else {
    $toolsDir = Join-Path $RuntimeDir 'tools'
    $uvExe = Join-Path $toolsDir 'uv.exe'
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    if (-not (Test-Path -LiteralPath $uvExe)) {
        Write-Host '[1/5] Downloading uv 0.13.6...'
        $metadata = Invoke-RestMethod -TimeoutSec 30 'https://pypi.org/pypi/uv/0.13.6/json'
        $wheel = @($metadata.urls | Where-Object { $_.filename -eq 'uv-0.13.6-py3-none-win_amd64.whl' })
        if ($wheel.Count -ne 1) { throw 'The official Windows x64 uv wheel was not found.' }
        $archive = Join-Path $toolsDir 'uv-wheel.zip'
        $systemCurl = Join-Path $env:SystemRoot 'System32\curl.exe'
        if (Test-Path -LiteralPath $systemCurl) {
            & $systemCurl -4 --fail --location --silent --show-error --connect-timeout 20 --speed-time 60 --speed-limit 1024 --retry 2 --continue-at - --output $archive $wheel[0].url
            if ($LASTEXITCODE -ne 0) { throw 'Official uv download failed. Check connectivity to files.pythonhosted.org and retry.' }
        } else {
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri $wheel[0].url -OutFile $archive
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $wheel[0].digests.sha256) { throw 'uv checksum mismatch.' }
        $extraction = Join-Path $toolsDir 'uv-wheel'
        Expand-Archive -LiteralPath $archive -DestinationPath $extraction -Force
        $binary = @(Get-ChildItem -LiteralPath $extraction -Filter uv.exe -Recurse -File)
        if ($binary.Count -ne 1) { throw 'uv.exe missing from the verified wheel.' }
        Copy-Item -LiteralPath $binary[0].FullName -Destination $uvExe -Force
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeDir 'python'
    $venv = Join-Path $RuntimeDir 'venv'
    $pythonExe = Join-Path $venv 'Scripts\python.exe'
    Write-Host '[2/5] Preparing Python 3.11...'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        & $uvExe venv --python 3.11 --managed-python $venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    }
    Write-Host '[3/5] Installing isolated dependencies...'
    & $uvExe pip install --python $pythonExe --requirement (Join-Path $project 'requirements-web.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
    if (-not $BrowseOnly) {
        $gpu = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
        if (-not $gpu) {
            Write-Warning 'No NVIDIA driver tool found. Installing browse/edit mode only. Run again without -BrowseOnly after installing an NVIDIA driver.'
            $BrowseOnly = $true
        } else {
            & $uvExe pip install --python $pythonExe torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu126
            if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed.' }
            & $uvExe pip install --python $pythonExe --requirement (Join-Path $project 'requirements.txt')
            if ($LASTEXITCODE -ne 0) { throw 'Classification dependency installation failed.' }
            & $pythonExe -c 'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
            if ($LASTEXITCODE -ne 0) { throw 'CUDA check failed. Use -BrowseOnly for browsing and editing without inference.' }
        }
    }
}
Write-Host '[4/5] Verifying models, names and local configuration...'
$setupArgs = @('-B','-u','-X','utf8',(Join-Path $project 'tools\setup_runtime.py'),'--runtime-dir',$RuntimeDir,'--library',$LibraryPath)
if ($BrowseOnly) { $setupArgs += '--browse-only' }
if ($NoLaunch) { $setupArgs += '--no-launch' }
if ($NoShortcut) { $setupArgs += '--no-shortcut' }
if ($SkipDownloads) { $setupArgs += '--skip-downloads' }
if (Test-Path -LiteralPath (Join-Path $project 'plugins')) { $setupArgs += @('--qq-agent-dir',$QQAgentDir) }
& $pythonExe @setupArgs
if ($LASTEXITCODE -ne 0) { throw 'Setup failed. Re-run the same command to resume verified downloads.' }
Write-Host '[5/5] Ready. Double-click start.cmd to open the local library.'
