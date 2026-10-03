<#
  Stops the FinSight web app, API and app window that start-finsight.ps1 launched.
  Ollama (the model server) and SQL Server LocalDB are left running: other programs may use them.
#>
$ErrorActionPreference = "Continue"

function Stop-Tree($processId) { & taskkill /PID $processId /T /F 2>$null | Out-Null }

Write-Host ""
Write-Host "  Stopping FinSight..." -ForegroundColor Cyan

# 1 - the two console windows the launcher opened (killing them takes their servers with them)
$stopped = 0
foreach ($title in "FinSight API", "FinSight Web") {
    Get-CimInstance Win32_Process -Filter "Name = 'cmd.exe'" |
        Where-Object { $_.CommandLine -like "*title $title*" } |
        ForEach-Object { Stop-Tree $_.ProcessId; $stopped++ }
}

# 2 - the FinSight app window (its own browser profile, so this never touches your normal browser)
Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe' OR Name = 'chrome.exe'" |
    Where-Object { $_.CommandLine -like "*FinSight\app-window*" -and $_.CommandLine -notmatch '--type=' } |
    ForEach-Object { Stop-Tree $_.ProcessId }

# 3 - anything still listening on the FinSight ports (a server started some other way)
foreach ($port in 8000, 5173) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Tree $_; $stopped++ }
}

Start-Sleep -Seconds 1
$still = @(8000, 5173) | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue }
if ($still) { Write-Host ("  Still in use: port " + ($still -join ", ")) -ForegroundColor Yellow }
else        { Write-Host "  FinSight stopped. (Ollama and the database were left running.)" -ForegroundColor Green }
Write-Host ""
Start-Sleep -Seconds 3
