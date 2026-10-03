<#
  One-click start for FinSight AI.

    launcher\start-finsight.bat             (what the desktop shortcut runs)
    launcher\start-finsight.ps1 -NoBrowser  start everything but do not open the browser
    launcher\start-finsight.ps1 -Warmup     also pre-load the vision model into GPU memory
                                            (skipped when Windows is short of memory)
    launcher\start-finsight.ps1 -DefaultBrowser  open in your normal browser instead of the app window

  FinSight opens in its own app window: Edge (or Chrome) with a private profile kept in
  %LOCALAPPDATA%\FinSight\app-window, so the state of your everyday browser - its extensions, a
  pending update, a stuck session - cannot stop the app from loading.

  Starts, in order, anything that is not already running:
    1. Ollama              local model server              :11434
    2. SQL Server LocalDB  instance MSSQLLocalDB  - checked with a real query, repaired if stuck
    3. FinSight API        api\run.bat (FastAPI + agents)   :8000  -> minimised window "FinSight API"
    4. FinSight web app    npm run dev (Vite)               :5173  -> minimised window "FinSight Web"
  then waits until the whole chain answers (web app -> API -> database) and opens
  http://localhost:5173. Safe to run again: anything already running is left alone.
  Close the two minimised windows (or run stop-finsight.bat) to stop the app.
#>
param([switch]$NoBrowser, [switch]$Warmup, [switch]$NoWarmup,   # -NoWarmup: accepted for old shortcuts, now the default
      [switch]$DefaultBrowser)

$ErrorActionPreference = "Stop"
$root       = Split-Path -Parent $PSScriptRoot
$webUrl     = "http://localhost:5173/"
$apiHealth  = "http://127.0.0.1:8000/agents/health"
$dbHealth   = "http://127.0.0.1:8000/lending/health"
$ollamaUrl  = "http://127.0.0.1:11434/api/tags"
$model      = "qwen2.5vl:7b"       # default vision model (Settings > AI Models can change it)
$instance   = "MSSQLLocalDB"
$started    = Get-Date

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Step($text) { Write-Host ("  {0,-38}" -f $text) -NoNewline }
function Ok($text = "ok")   { Write-Host ("[ {0} ]" -f $text) -ForegroundColor Green }
function Note($text)        { Write-Host ("[ {0} ]" -f $text) -ForegroundColor Yellow }

function Test-Url($url, $timeout = 3) {
    try { $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeout; return $true } catch { return $false }
}
function Test-Port($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}
function Wait-Url($url, $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        if (Test-Url $url) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}
function Elapsed { return ("{0:N0} s" -f ((Get-Date) - $started).TotalSeconds) }

# Open the app in its own window with its own browser profile, independent of the everyday browser.
# Returns a description of what was opened.
$appProfile = Join-Path $env:LOCALAPPDATA "FinSight\app-window"
function Open-AppWindow([string[]]$extraArgs = @()) {
    if (-not $DefaultBrowser) {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
        $exe = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if ($exe) {
            New-Item -ItemType Directory -Force $appProfile | Out-Null
            # no sync / implicit sign-in: otherwise Edge signs a new profile into the Windows Microsoft
            # account and pops up a "we are now syncing your browsing data" dialog over the app
            $appArgs = @("--app=$webUrl", "--user-data-dir=`"$appProfile`"", "--no-first-run", "--no-default-browser-check",
                         "--disable-sync", "--disable-features=msImplicitSignin", "--start-maximized") + $extraArgs
            Start-Process -FilePath $exe -ArgumentList $appArgs
            return "app window (" + [IO.Path]::GetFileNameWithoutExtension($exe) + ")"
        }
    }
    Start-Process $webUrl
    return "your default browser"
}

# Memory that Windows can still promise to programs (RAM + page file). When it runs out, new
# processes fail to start ("The application was unable to start correctly", error 575) even though
# Task Manager shows free RAM - that is what made the database refuse to start.
function Get-FreeCommitGB {
    return [math]::Round((Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory / 1MB, 1)
}

# Run a console program and return its output as text. Windows PowerShell 5.1 turns anything a
# program writes to stderr into a terminating error when $ErrorActionPreference is "Stop" - which is
# how a LocalDB start failure used to abort the launcher before it could repair anything.
function Invoke-Native([scriptblock]$Command) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return @(& $Command 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $saved }
}

function Get-DatabaseState {
    $info = Invoke-Native { sqllocaldb info $instance }
    if ($info -match "State:\s+Running") { return "Running" } else { return "Stopped" }
}

# The sqlservr.exe processes serving this instance, whoever started them.
function Get-DatabaseServers {
    return @(Get-CimInstance Win32_Process -Filter "Name = 'sqlservr.exe'" |
        Where-Object { $_.CommandLine -match "\\Instances\\$instance\\" })
}

# A real query, not `sqllocaldb start`: that command prints an error but still returns exit code 0.
function Test-Database {
    if (-not (Get-Command sqlcmd -ErrorAction SilentlyContinue)) { return $true }   # cannot check; the API check later will
    $out = Invoke-Native { sqlcmd -S "(localdb)\$instance" -d FinSight -l 20 -h -1 -W -Q "SET NOCOUNT ON; SELECT 1" }
    return [bool]($out | Where-Object { $_.Trim() -eq "1" })
}

# LocalDB keeps track of its running server per Windows session context. A server started from
# somewhere else (a script, a terminal, an earlier crash) keeps running and holding the database
# files while this context sees the instance as "Stopped" and fails to start a second copy
# ("SQL Server process failed to start", error 575). Shut such a server down cleanly through its
# own pipe - a normal SHUTDOWN, so no data is lost - and start the instance again from here.
function Repair-Database {
    $servers = Get-DatabaseServers
    foreach ($s in $servers) {
        if ($s.CommandLine -match '-s(LOCALDB#[0-9A-Fa-f]+)') {
            $pipe = "np:\\.\pipe\" + $Matches[1] + "\tsql\query"
            $null = Invoke-Native { sqlcmd -S $pipe -l 10 -Q "SHUTDOWN" }
        }
        for ($i = 0; $i -lt 40 -and (Get-Process -Id $s.ProcessId -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
        if (Get-Process -Id $s.ProcessId -ErrorAction SilentlyContinue) { Stop-Process -Id $s.ProcessId -Force }  # did not answer: last resort
    }
    $null = Invoke-Native { sqllocaldb start $instance }
    return $servers.Count
}

try {
    Say ""
    Say "  FinSight AI" "Cyan"
    Say "  Starting the platform..."
    Say ""

    $freeGB = Get-FreeCommitGB
    if ($freeGB -lt 4) {
        Say ("  Windows is low on memory ({0} GB left). If something fails to start, close a few programs" -f $freeGB) "Yellow"
        Say "  (browser windows with many tabs, WSL / Docker, games) and run FinSight again." "Yellow"
        Say ""
    }

    # 1 - Ollama -------------------------------------------------------------
    Step "Local model server (Ollama)"
    if (Test-Url $ollamaUrl) { Ok "running" }
    else {
        $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
        if (-not $ollama) {
            $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
            if (Test-Path $candidate) { $ollama = $candidate }
        }
        if (-not $ollama) { Note "not installed - document reading will not work" }
        else {
            Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
            if (Wait-Url $ollamaUrl 40) { Ok "started" } else { Note "did not answer" }
        }
    }

    # 2 - SQL Server LocalDB ---------------------------------------------------
    Step "Database (SQL Server LocalDB)"
    if (-not (Get-Command sqllocaldb -ErrorAction SilentlyContinue)) {
        throw "SQL Server LocalDB is not installed. Install it (SQL Server Express LocalDB) and run docs\mssql\*.sql."
    }
    $repaired = 0
    if ((Get-DatabaseState) -eq "Stopped" -and (Get-DatabaseServers).Count -gt 0) {
        # a server is running that this context cannot see: take it over straight away
        $repaired = Repair-Database
    } else {
        $null = Invoke-Native { sqllocaldb start $instance }
    }
    if (Test-Database) { Ok ($(if ($repaired) { "recovered" } else { "running" })) }
    else {
        $fixed = Repair-Database
        if (Test-Database) { Ok ($(if ($fixed) { "recovered" } else { "started" })) }
        else {
            throw ("The database did not start. Windows has {0} GB of memory left for programs - close a few " -f (Get-FreeCommitGB)) +
                  "programs and try again. (Details: Windows Event Viewer > Application log, source SQLLocalDB.)"
        }
    }

    # 3 + 4 - launch the API and the web app (each in its own minimised window) -----
    $apiUp = Test-Url $apiHealth
    $webUp = Test-Url $webUrl

    if (-not $apiUp -and -not (Test-Port 8000)) {
        # full path + call: some Windows setups do not search the current folder for batch files
        $apiBat = Join-Path $root "api\run.bat"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/k title FinSight API && call `"$apiBat`"" `
                      -WorkingDirectory (Join-Path $root "api") -WindowStyle Minimized
    }
    if (-not $webUp -and -not (Test-Port 5173)) {
        if (-not (Test-Path (Join-Path $root "node_modules"))) {
            Say "  Installing web dependencies (first run only)..." "Yellow"
            Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm install" -WorkingDirectory $root -Wait -NoNewWindow
        }
        Start-Process -FilePath "cmd.exe" -ArgumentList "/k title FinSight Web && npm run dev" `
                      -WorkingDirectory $root -WindowStyle Minimized
    }

    Step "API (FastAPI + 29 agents)"
    if ($apiUp) { Ok "already running" }
    else {
        # the first ever run creates the virtual environment and installs packages (minutes)
        $limit = 600; if (Test-Path (Join-Path $root "api\.venv-agents")) { $limit = 150 }
        if (Wait-Url $apiHealth $limit) { Ok ("ready, " + (Elapsed)) }
        else { throw "The API did not start. Look at the 'FinSight API' window in the taskbar for the error." }
    }

    # the API's own view of the database: this is what the app waits on after you pick a role
    Step "API -> database connection"
    $db = $null
    for ($i = 0; $i -lt 3 -and -not $db; $i++) {
        try { $db = Invoke-RestMethod -Uri $dbHealth -TimeoutSec 30 } catch { Start-Sleep -Seconds 2 }
    }
    if ($db -and $db.ok) { Ok ("{0} application(s)" -f $db.applications) }
    else { throw "The API cannot reach the database. Run FinSight again; if it repeats, restart the computer." }

    Step "Web app (React + Vite)"
    if (-not $webUp) {
        if (-not (Wait-Url $webUrl 90)) { throw "The web app did not start. Look at the 'FinSight Web' window in the taskbar for the error." }
    }
    # Compile the whole app before the browser opens. Vite builds each file the first time it is
    # asked for; with the API starting at the same moment that took up to a minute, all of it spent
    # on a blank browser page. Asking for every file here moves that wait into this window.
    if (-not $webUp) {
        $files = Get-ChildItem (Join-Path $root "src") -Recurse -File -Include *.jsx, *.js |
            ForEach-Object { $webUrl + ($_.FullName.Substring($root.Length + 1) -replace '\\', '/') }
        $curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
        if ($curl) {
            $curlArgs = @("-s", "--parallel", "--parallel-max", "6", "-m", "180")
            foreach ($f in $files) { $curlArgs += @("-o", "NUL", $f) }
            $null = Invoke-Native { & $curl @curlArgs }
        } else {
            foreach ($f in $files) { $null = Test-Url $f 120 }
        }
    }
    Ok $(if ($webUp) { "already running" } else { "ready, " + (Elapsed) })

    # optional: pre-load the vision model so the first document upload is fast. It takes ~8 GB of
    # memory, so it only happens when asked for and when Windows has room for it.
    if ($Warmup -and (Test-Url $ollamaUrl)) {
        Step "Vision model ($model)"
        $freeGB = Get-FreeCommitGB
        if ($freeGB -lt 12) { Note ("skipped - only {0} GB free" -f $freeGB) }
        else {
            $body = '{"model":"' + $model + '","prompt":"","keep_alive":"15m","options":{"num_ctx":8192}}'
            $tmp  = Join-Path $env:TEMP "finsight-warmup.json"
            [IO.File]::WriteAllText($tmp, $body)
            Start-Process -FilePath "curl.exe" -WindowStyle Hidden `
                -ArgumentList "-s", "-m", "300", "-X", "POST", "http://127.0.0.1:11434/api/generate", "-d", "@$tmp", "-o", "nul"
            Note "loading in background"
        }
    }

    Say ""
    Say "  FinSight is running  ->  $webUrl" "Green"
    if (-not $NoBrowser) { Say ("  Opened in " + (Open-AppWindow)) "Green" }
    Say "  To stop it: run launcher\stop-finsight.bat, or close the two minimised windows ('FinSight API', 'FinSight Web')" "DarkGray"
    Say ""
    Start-Sleep -Seconds 4
}
catch {
    Say ""
    Say ("  Could not start FinSight: " + $_.Exception.Message) "Red"
    Say ""
    Read-Host "  Press Enter to close" | Out-Null
    exit 1
}
