[CmdletBinding()]
param(
    [string]$ConfigPath = "$env:ProgramData\RPMEdgeProxy\watchdog-config.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$dataDir = Split-Path -Parent $ConfigPath
$pausePath = Join-Path $dataDir "watchdog.pause"
$statePath = Join-Path $dataDir "watchdog-state.json"
$logPath = Join-Path $dataDir "watchdog.log"

function Write-WatchdogLog {
    param([string]$Message)

    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    & docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Start-DockerDesktop {
    param($Config)

    $service = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne "Running") {
        try { Start-Service -Name $service.Name -ErrorAction Stop } catch { Write-WatchdogLog "Docker service start failed: $($_.Exception.Message)" }
    }

    $desktopStartFailed = $true
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        & docker desktop start *> $null
        $desktopStartFailed = $LASTEXITCODE -ne 0
    }
    if ($desktopStartFailed -and $Config.DockerDesktopPath -and (Test-Path $Config.DockerDesktopPath)) {
        Start-Process -FilePath $Config.DockerDesktopPath -WindowStyle Minimized
    }
}

function Get-WatchdogState {
    if (Test-Path $statePath) {
        try { return Get-Content $statePath -Raw | ConvertFrom-Json } catch { }
    }
    return [pscustomobject]@{
        ConsecutiveFailures = 0
        LastAutomaticReboot = $null
        LastSuccess = $null
    }
}

function Save-WatchdogState {
    param($State)
    $State | ConvertTo-Json -Depth 4 | Set-Content -Path $statePath -Encoding UTF8
}

if (Test-Path $pausePath) {
    Write-WatchdogLog "Paused by $pausePath"
    exit 0
}
if (-not (Test-Path $ConfigPath -PathType Leaf)) {
    throw "Watchdog configuration not found: $ConfigPath"
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$state = Get-WatchdogState
$healthy = $false

try {
    if (-not (Test-DockerReady)) {
        Write-WatchdogLog "Docker is unavailable; attempting Docker Desktop recovery."
        Start-DockerDesktop -Config $config
        Start-Sleep -Seconds 30
    }

    if (Test-DockerReady) {
        if (Test-Path $config.ComposePath -PathType Leaf) {
            & docker compose --file $config.ComposePath --project-directory $config.ProjectPath `
                up --detach --remove-orphans *> $null
            if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
        }

        foreach ($containerName in @($config.Containers)) {
            & docker inspect $containerName *> $null
            if ($LASTEXITCODE -ne 0) { throw "tracked container '$containerName' is missing" }
            $running = (& docker inspect --format '{{.State.Running}}' $containerName).Trim()
            if ($running -ne "true") {
                & docker start $containerName *> $null
                if ($LASTEXITCODE -ne 0) { throw "tracked container '$containerName' cannot start" }
            }
        }

        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $config.HealthUrl -TimeoutSec 5
            if ($response.StatusCode -ne 200) { throw "RPM proxy health returned HTTP $($response.StatusCode)" }
        } catch {
            throw "RPM proxy health check failed: $($_.Exception.Message)"
        }
        $healthy = $true
    }
} catch {
    Write-WatchdogLog "Recovery check failed: $($_.Exception.Message)"
}

if ($healthy) {
    if ([int]$state.ConsecutiveFailures -gt 0) {
        Write-WatchdogLog "Recovered after $($state.ConsecutiveFailures) failed check(s)."
    }
    $state.ConsecutiveFailures = 0
    $state.LastSuccess = (Get-Date).ToString("o")
    Save-WatchdogState -State $state
    exit 0
}

$state.ConsecutiveFailures = [int]$state.ConsecutiveFailures + 1
Write-WatchdogLog "Consecutive failure $($state.ConsecutiveFailures) of $($config.RebootAfterFailures)."
Save-WatchdogState -State $state

if ([int]$config.RebootAfterFailures -eq 0 -or [int]$state.ConsecutiveFailures -lt [int]$config.RebootAfterFailures) {
    exit 1
}

$uptime = (Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
if ($uptime.TotalMinutes -lt [int]$config.MinimumUptimeBeforeRebootMinutes) {
    Write-WatchdogLog "Automatic reboot suppressed: uptime is only $([int]$uptime.TotalMinutes) minutes."
    exit 1
}

$lastReboot = $null
if ($state.LastAutomaticReboot) {
    $lastReboot = [datetime]::Parse($state.LastAutomaticReboot)
}
if ($lastReboot -and ((Get-Date) - $lastReboot).TotalHours -lt [int]$config.MinimumHoursBetweenAutomaticReboots) {
    Write-WatchdogLog "Automatic reboot suppressed by the six-hour reboot-loop guard."
    exit 1
}

$state.LastAutomaticReboot = (Get-Date).ToString("o")
Save-WatchdogState -State $state
Write-WatchdogLog "Scheduling a guarded Windows recovery reboot in 60 seconds."
& shutdown.exe /r /t 60 /f /c "RPM server watchdog could not recover Docker or its tracked containers." | Out-Null
