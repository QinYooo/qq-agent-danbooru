param([string]$PythonPath,[string]$ProjectDir)
$ErrorActionPreference = 'Stop'
$pythonw = Join-Path (Split-Path $PythonPath -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $PythonPath }
$name = if (Test-Path -LiteralPath (Join-Path $ProjectDir 'plugins')) { 'Danbooru QQ Toolkit.lnk' } else { 'Danbooru Local Manager.lnk' }
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop $name))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-B "' + (Join-Path $ProjectDir 'start.py') + '"'
$shortcut.WorkingDirectory = $ProjectDir
$shortcut.WindowStyle = 7
$shortcut.Description = 'Local Danbooru image library'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',141'
$shortcut.Save()
