<#
  Puts a "FinSight AI" shortcut on your Desktop that starts the whole project.
  Run it once (or again if you move the project folder):

      powershell -ExecutionPolicy Bypass -File launcher\create-desktop-shortcut.ps1
#>
$root    = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")          # follows OneDrive if the Desktop is redirected
$lnkPath = Join-Path $desktop "FinSight AI.lnk"
$icon    = Join-Path $root "docs\brand\finsight.ico"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath       = Join-Path $PSScriptRoot "start-finsight.bat"
$lnk.WorkingDirectory = $root
if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
$lnk.Description      = "Start FinSight AI: web app, API, local model and database"
$lnk.WindowStyle      = 1
$lnk.Save()

Write-Host "Created: $lnkPath"
