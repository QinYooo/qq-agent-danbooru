param([Parameter(Mandatory=$true)][string]$ProjectDir,[switch]$StopServer)
$ErrorActionPreference = 'Stop'
$configFile = Join-Path $ProjectDir 'config.json'
if (-not (Test-Path -LiteralPath $configFile)) { throw 'Run install.cmd first.' }
$config = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
$scriptName = if ($StopServer) { 'stop.py' } else { 'start.py' }
$python = $config.python
$pythonw = Join-Path (Split-Path $python -Parent) 'pythonw.exe'
if (Test-Path -LiteralPath $pythonw) { $python = $pythonw }
$scriptFile = Join-Path $ProjectDir $scriptName
Start-Process -FilePath $python -ArgumentList ('-B "' + $scriptFile + '"') -WorkingDirectory $ProjectDir -WindowStyle Hidden
