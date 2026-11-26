# Veracity SQL developer commands. Run from anywhere: .\scripts\dev.ps1 <command>
#
#   setup    create .venv (Python 3.11), install Python and web packages; create .env from .env.example
#   start    stop leftovers, start engine + api (:8100) + answer (:8200) + web (Vite :5180) in this window,
#            wait until healthy, open the browser. Ctrl+C stops everything.
#            options: -SkipEngine -SkipAnswer -SkipWeb; -Prod (APP_ENV=prod, secrets from .env.prod)
#   stop     stop everything start left running
#   status   show what is running, on which ports
#   logs     follow a service's log file (logs\<service>.log); option: -Service engine|api|answer
#   test     run the test suite (pytest)
#   build    build the four production images and save them to dist\<prefix>-<timestamp>.tar (prefix: IMAGE_PREFIX, default veracity-sql)

param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "start", "stop", "status", "logs", "test", "build", "help")]
    [string]$Command = "help",

    [switch]$Prod,
    [switch]$SkipEngine,
    [switch]$SkipAnswer,
    [switch]$SkipWeb,

    [ValidateSet("engine", "api", "answer")]
    [string]$Service = "engine"
)

# Continue, not Stop: Windows PowerShell 5.1 turns any stderr line from a native
# tool (pip/npm/docker warnings) into a terminating error under Stop. Every native
# call below checks $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Web = Join-Path $Root "web"
$PidFile = Join-Path $Root ".dev-pids"
# Image prefix: IMAGE_PREFIX from the environment or .env, else veracity-sql (same rule as docker compose).
$Prefix = $env:IMAGE_PREFIX
if (-not $Prefix -and (Test-Path (Join-Path $Root ".env"))) {
    $m = Select-String -Path (Join-Path $Root ".env") -Pattern '^\s*IMAGE_PREFIX\s*=\s*(\S+)' | Select-Object -First 1
    if ($m) { $Prefix = $m.Matches[0].Groups[1].Value.Trim('"', "'") }
}
if (-not $Prefix) { $Prefix = "veracity-sql" }
$Images = "engine", "api", "answer", "web" | ForEach-Object { "$Prefix-${_}:latest" }
Set-Location $Root

function Say($text, $color = "Cyan") { Write-Host $text -ForegroundColor $color }

# Ports come from config.yaml (catalog.api.port, catalog.answer.port, catalog.web.dev_port)
# through the same loader the services use, so there is no second copy here.
function Get-DevSettings {
    $settings = @{}
    foreach ($line in (& $Py (Join-Path $Root "scripts\_print_dev_env.py"))) {
        if ($line -match '^([A-Z_]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2] }
    }
    if ($LASTEXITCODE) { throw "Could not load config\config.yaml - see the error above." }
    return $settings
}

function Stop-Tree($id) { & taskkill.exe /PID $id /T /F 2>$null | Out-Null }

function Stop-All($s) {
    if (Test-Path $PidFile) {
        foreach ($id in Get-Content $PidFile) { Stop-Tree $id }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    foreach ($port in $s.API_PORT, $s.ANSWER_PORT, $s.WEB_DEV_PORT) {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { if ($_.OwningProcess -ne $PID) { Stop-Tree $_.OwningProcess } }
    }
    # The engine binds no port: find a leftover one by its command line, limited to this
    # project's own interpreter so no unrelated process is touched.
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $Py -and $_.CommandLine -like "*engine.main*" } |
        ForEach-Object { Stop-Tree $_.ProcessId }
}

function Wait-Http($url, $seconds = 90) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

function Assert-Setup {
    if (-not (Test-Path $Py)) { throw ".venv missing - run: .\scripts\dev.ps1 setup" }
    $envFile = if ($Prod) { ".env.prod" } else { ".env" }
    if (-not (Test-Path (Join-Path $Root $envFile))) { throw "$envFile missing - run: .\scripts\dev.ps1 setup" }
}

switch ($Command) {
    "setup" {
        if (-not (Test-Path $Py)) {
            Say "Creating .venv (Python 3.11)..."
            & py -3.11 -m venv (Join-Path $Root ".venv")
            if ($LASTEXITCODE -or -not (Test-Path $Py)) { throw "Could not create the venv - is Python 3.11 installed (py -3.11)?" }
        }
        Say "Installing Python packages (requirements.txt + requirements-dev-test.txt)..."
        & $Py -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-dev-test.txt
        if ($LASTEXITCODE) { throw "pip install failed." }
        Say "Installing web packages..."
        Push-Location $Web; try { & npm ci } finally { Pop-Location }
        if ($LASTEXITCODE) { throw "npm ci failed." }
        if (-not (Test-Path (Join-Path $Root ".env"))) {
            Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
            Say "Created .env from .env.example: fill in the secrets. Every other setting is in config\config.yaml." Yellow
        }
        Say "pyodbc also needs Microsoft ODBC Driver 17 or 18 for SQL Server installed on this PC." Yellow
        Say "Setup complete. Next: .\scripts\dev.ps1 start" Green
    }

    "start" {
        Assert-Setup
        if ($Prod) { $env:APP_ENV = "prod"; $env:ENV_FILE = ".env.prod" } else { $env:APP_ENV = "dev"; $env:ENV_FILE = ".env" }
        $s = Get-DevSettings
        if ($s.CATALOG_ENABLED -ne "true") { Say "catalog.enabled is false: the engine will idle and the API reports DISABLED." Yellow }
        if ($s.SOURCES_CONFIGURED -eq "0") { Say "No sources under catalog.sources: discovery has nothing to do yet." Yellow }
        Say "Stopping anything left from a previous start..."
        Stop-All $s
        $procs = @()
        if (-not $SkipEngine) {
            $procs += Start-Process -FilePath $Py -ArgumentList "-m", "engine.main" -WorkingDirectory $Root -NoNewWindow -PassThru
        }
        $procs += Start-Process -FilePath $Py -WorkingDirectory $Root -NoNewWindow -PassThru `
            -ArgumentList "-m", "uvicorn", "api.main:app", "--reload", "--host", $s.API_HOST, "--port", $s.API_PORT
        if (-not $SkipAnswer) {
            $procs += Start-Process -FilePath $Py -WorkingDirectory $Root -NoNewWindow -PassThru `
                -ArgumentList "-m", "uvicorn", "answer.main:app", "--reload", "--host", $s.ANSWER_HOST, "--port", $s.ANSWER_PORT
        }
        if (-not $SkipWeb) {
            # Read by vite.config.ts / src/api.ts.
            $env:VITE_API_BASE_URL = $s.WEB_API_BASE_URL
            $env:VITE_ANSWER_BASE_URL = $s.WEB_ANSWER_BASE_URL
            $env:VITE_DEV_PORT = $s.WEB_DEV_PORT
            $procs += Start-Process -FilePath "cmd.exe" -WorkingDirectory $Web -NoNewWindow -PassThru -ArgumentList "/c", "npm run dev"
        }
        Set-Content $PidFile ($procs | ForEach-Object { $_.Id })
        try {
            if (-not (Wait-Http "http://localhost:$($s.API_PORT)/health")) { throw "API did not answer on port $($s.API_PORT) - see the output above." }
            if (-not $SkipAnswer -and -not (Wait-Http "http://localhost:$($s.ANSWER_PORT)/health")) { throw "Answer service did not answer on port $($s.ANSWER_PORT) - see the output above." }
            if (-not $SkipWeb -and -not (Wait-Http "http://localhost:$($s.WEB_DEV_PORT)")) { throw "Web UI did not answer on port $($s.WEB_DEV_PORT) - see the output above." }
            Say "Ready. API http://localhost:$($s.API_PORT)/docs  Answer http://localhost:$($s.ANSWER_PORT)/docs  Logs $Root\$($s.LOG_DIR)" Green
            if (-not $SkipWeb) {
                Say "Web UI http://localhost:$($s.WEB_DEV_PORT). Ctrl+C stops everything." Green
                Start-Process "http://localhost:$($s.WEB_DEV_PORT)"
            }
            while (-not ($procs | Where-Object { $_.HasExited })) { Start-Sleep -Seconds 1 }
            Say "A process exited - stopping the rest." Yellow
        }
        finally { Stop-All $s }
    }

    "stop" { Stop-All (Get-DevSettings); Say "Stopped." Green }

    "status" {
        $s = Get-DevSettings
        foreach ($p in @(@("api", $s.API_PORT), @("answer", $s.ANSWER_PORT), @("web", $s.WEB_DEV_PORT))) {
            $conn = Get-NetTCPConnection -LocalPort $p[1] -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($conn) { Say ("{0,-7} port {1}: running (PID {2})" -f $p[0], $p[1], $conn.OwningProcess) Green }
            else { Say ("{0,-7} port {1}: not running" -f $p[0], $p[1]) Yellow }
        }
        $engine = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -eq $Py -and $_.CommandLine -like "*engine.main*" } | Select-Object -First 1
        if ($engine) { Say ("engine  (no port) : running (PID {0})" -f $engine.ProcessId) Green } else { Say "engine  (no port) : not running" Yellow }
    }

    "logs" {
        $log = Join-Path $Root "logs\$Service.log"
        if (-not (Test-Path $log)) { throw "$log does not exist yet - start the services first." }
        Get-Content $log -Tail 50 -Wait
    }

    "test" {
        if (-not (Test-Path $Py)) { throw ".venv missing - run: .\scripts\dev.ps1 setup" }
        # tests/test_access_api.py runs only when CATALOG_TEST_CONFIG points at a config
        # whose catalog database is disposable; otherwise it is skipped.
        & $Py -m pytest tests -q
        if ($LASTEXITCODE) { throw "Tests failed." }
        Say "Tests passed." Green
    }

    "build" {
        # Context is the repo root; .dockerignore keeps .venv, node_modules, logs, *.tar and
        # every .env* out of it.
        Say "Building $($Images -join ', ')..."
        & docker compose -f (Join-Path $Root "docker-compose.yaml") build
        if ($LASTEXITCODE) { throw "docker compose build failed." }
        $dist = Join-Path $Root "dist"
        New-Item -ItemType Directory -Force $dist | Out-Null
        $tar = Join-Path $dist ("$Prefix-{0}.tar" -f (Get-Date -Format "yyyyMMdd-HHmm"))
        Say "Saving the images to $tar ..."
        & docker save -o $tar @Images
        if ($LASTEXITCODE) { throw "docker save failed." }
        Say ("Done: {0} ({1:N0} MB)" -f $tar, ((Get-Item $tar).Length / 1MB)) Green
        Say "Copy to the target: the .tar, docker-compose.prod.yaml, .env.prod.example, config\, FIRST_DEPLOYMENT.md" Green
    }

    default { Get-Content $PSCommandPath -TotalCount 12 | ForEach-Object { $_ -replace "^# ?", "" } }
}
