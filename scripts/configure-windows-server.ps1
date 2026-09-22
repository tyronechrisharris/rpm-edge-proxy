[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet",
    [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot),
    [ValidateRange(1, 30)]
    [int]$WatchdogIntervalMinutes = 2,
    [ValidateRange(0, 30)]
    [int]$RebootAfterFailures = 8
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window (Run as Administrator)."
}

$sourceProjectPath = (Resolve-Path $ProjectPath).Path
$composePath = Join-Path $sourceProjectPath "compose.yaml"
if (-not (Test-Path $composePath -PathType Leaf)) {
    throw "compose.yaml was not found under '$sourceProjectPath'."
}
if (-not (Get-NetAdapter -Name $InterfaceAlias -ErrorAction SilentlyContinue)) {
    throw "Network adapter '$InterfaceAlias' does not exist."
}

$dataDir = Join-Path $env:ProgramData "RPMEdgeProxy"
$runtimeProjectPath = Join-Path $dataDir "app"
$statePath = Join-Path $dataDir "server-baseline.json"
$watchdogConfigPath = Join-Path $dataDir "watchdog-config.json"
$installedWatchdogPath = Join-Path $dataDir "windows-server-watchdog.ps1"
$taskName = "RPM Edge Proxy Server Watchdog"

if (Test-Path $statePath) {
    throw "Server mode is already configured. Run restore-windows-server.ps1 before applying it again."
}

New-Item -Path $dataDir -ItemType Directory -Force | Out-Null
New-Item -Path $runtimeProjectPath -ItemType Directory -Force | Out-Null
foreach ($deploymentItem in @(".dockerignore", "Dockerfile", "compose.yaml", "cas_proxy", "config")) {
    $sourceItem = Join-Path $sourceProjectPath $deploymentItem
    if (Test-Path $sourceItem) {
        Copy-Item -Path $sourceItem -Destination $runtimeProjectPath -Recurse -Force
    }
}
$ProjectPath = $runtimeProjectPath
$composePath = Join-Path $ProjectPath "compose.yaml"

function Get-PowerSchemeGuid {
    $text = (& powercfg.exe /getactivescheme) -join " "
    if ($LASTEXITCODE -ne 0) { throw "Cannot read the active Windows power scheme." }
    $match = [regex]::Match($text, "[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
    if (-not $match.Success) { throw "Cannot parse the active Windows power scheme GUID." }
    return $match.Value
}

function Get-RegistryBaseline {
    param([string]$Path, [string]$Name)

    if (-not (Test-Path $Path)) {
        return [ordered]@{ Path = $Path; Name = $Name; Exists = $false; Value = $null; Kind = $null }
    }
    $key = Get-Item -Path $Path
    $exists = $key.GetValueNames() -contains $Name
    if (-not $exists) {
        return [ordered]@{ Path = $Path; Name = $Name; Exists = $false; Value = $null; Kind = $null }
    }
    return [ordered]@{
        Path = $Path
        Name = $Name
        Exists = $true
        Value = $key.GetValue($Name, $null, "DoNotExpandEnvironmentNames")
        Kind = $key.GetValueKind($Name).ToString()
    }
}

function Set-Dword {
    param([string]$Path, [string]$Name, [int]$Value)

    New-Item -Path $Path -Force | Out-Null
    New-ItemProperty -Path $Path -Name $Name -PropertyType DWord -Value $Value -Force | Out-Null
}

function Invoke-PowerCfg {
    param([string[]]$Arguments)

    & powercfg.exe @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "powercfg failed: $($Arguments -join ' ')"
    }
}

$desiredRegistry = @(
    @{ Path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling"; Name = "PowerThrottlingOff"; Value = 1 },
    @{ Path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling"; Name = "DisableUserPresenceQos"; Value = 1 },
    @{ Path = "HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl"; Name = "AutoReboot"; Value = 1 },
    @{ Path = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"; Name = "HiberbootEnabled"; Value = 0 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"; Name = "NoAutoUpdate"; Value = 0 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"; Name = "AUOptions"; Value = 3 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"; Name = "NoAutoRebootWithLoggedOnUsers"; Value = 1 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"; Name = "SleepingTabsEnabled"; Value = 0 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"; Name = "EfficiencyModeEnabled"; Value = 0 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"; Name = "EfficiencyMode"; Value = 1 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"; Name = "BackgroundModeEnabled"; Value = 1 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Google\Chrome"; Name = "HighEfficiencyModeEnabled"; Value = 0 },
    @{ Path = "HKLM:\SOFTWARE\Policies\Google\Chrome"; Name = "BackgroundModeEnabled"; Value = 1 }
)

$originalPowerScheme = Get-PowerSchemeGuid
$adapterPower = Get-NetAdapterPowerManagement -Name $InterfaceAlias -ErrorAction SilentlyContinue
$adapterPowerBaseline = $null
if ($adapterPower) {
    $adapterPowerBaseline = [ordered]@{
        SelectiveSuspend = $adapterPower.SelectiveSuspend.ToString()
        DeviceSleepOnDisconnect = $adapterPower.DeviceSleepOnDisconnect.ToString()
    }
}
$state = [ordered]@{
    Version = 1
    ConfiguredAt = (Get-Date).ToString("o")
    SourceProjectPath = $sourceProjectPath
    ProjectPath = $ProjectPath
    InterfaceAlias = $InterfaceAlias
    OriginalPowerScheme = $originalPowerScheme
    ServerPowerScheme = $null
    Registry = @($desiredRegistry | ForEach-Object { Get-RegistryBaseline -Path $_.Path -Name $_.Name })
    AdapterPower = $adapterPowerBaseline
    ContainerRestartPolicies = @()
}
$state | ConvertTo-Json -Depth 8 | Set-Content -Path $statePath -Encoding UTF8

$duplicateOutput = (& powercfg.exe /duplicatescheme $originalPowerScheme) -join " "
if ($LASTEXITCODE -ne 0) { throw "Cannot duplicate the active Windows power scheme." }
$duplicateMatch = [regex]::Match($duplicateOutput, "[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
if (-not $duplicateMatch.Success) { throw "Cannot parse the new Windows power scheme GUID." }
$serverPowerScheme = $duplicateMatch.Value
$state.ServerPowerScheme = $serverPowerScheme
$state | ConvertTo-Json -Depth 8 | Set-Content -Path $statePath -Encoding UTF8

Invoke-PowerCfg @("/changename", $serverPowerScheme, "RPM Server Always On", "Always-on profile for RPM proxy and OSCAR")

$subSleep = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
$standbyIdle = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
$hibernateIdle = "9d7815a6-7ee4-497e-8888-515a05f02364"
$subButtons = "4f971e89-eebd-4455-a8de-9e59040e7347"
$lidAction = "5ca83367-6e45-459f-a27b-476b1d01c936"
$subProcessor = "54533251-82be-4824-96c1-47b60b740d00"
$processorMin = "893dee8e-2bef-41e0-89c6-b55d0929964c"
$processorMax = "bc5038f7-23e0-4960-96da-33abaf5935ec"
$subUsb = "2a737441-1930-4402-8d77-b2bebba308a3"
$usbSelectiveSuspend = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"

foreach ($mode in @("/setacvalueindex", "/setdcvalueindex")) {
    Invoke-PowerCfg @($mode, $serverPowerScheme, $subSleep, $standbyIdle, "0")
    Invoke-PowerCfg @($mode, $serverPowerScheme, $subSleep, $hibernateIdle, "0")
    Invoke-PowerCfg @($mode, $serverPowerScheme, $subButtons, $lidAction, "0")
    Invoke-PowerCfg @($mode, $serverPowerScheme, $subProcessor, $processorMax, "100")
    Invoke-PowerCfg @($mode, $serverPowerScheme, $subUsb, $usbSelectiveSuspend, "0")
}
Invoke-PowerCfg @("/setacvalueindex", $serverPowerScheme, $subProcessor, $processorMin, "100")
Invoke-PowerCfg @("/setdcvalueindex", $serverPowerScheme, $subProcessor, $processorMin, "25")
Invoke-PowerCfg @("/setactive", $serverPowerScheme)
Invoke-PowerCfg @("/change", "monitor-timeout-ac", "15")
Invoke-PowerCfg @("/change", "monitor-timeout-dc", "5")
Invoke-PowerCfg @("/change", "disk-timeout-ac", "0")
Invoke-PowerCfg @("/change", "disk-timeout-dc", "0")

foreach ($entry in $desiredRegistry) {
    Set-Dword -Path $entry.Path -Name $entry.Name -Value $entry.Value
}

try {
    Set-NetAdapterPowerManagement -Name $InterfaceAlias -SelectiveSuspend Disabled `
        -DeviceSleepOnDisconnect Disabled -ErrorAction Stop | Out-Null
} catch {
    Write-Warning "The '$InterfaceAlias' driver does not expose all requested power controls: $($_.Exception.Message)"
}

$dockerReady = $false
$trackedContainers = @("rpm-edge-proxy")
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker info *> $null
    $dockerReady = $LASTEXITCODE -eq 0
}

if ($dockerReady) {
    Push-Location $ProjectPath
    try {
        & docker compose up --detach --remove-orphans
        if ($LASTEXITCODE -ne 0) { throw "Docker Compose could not start the RPM proxy." }
    } finally {
        Pop-Location
    }

    $runningIds = @(& docker ps --quiet)
    foreach ($containerId in $runningIds) {
        $name = ((& docker inspect --format '{{.Name}}' $containerId).Trim()).TrimStart("/")
        $policy = (& docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' $containerId).Trim()
        $state.ContainerRestartPolicies += [ordered]@{ Name = $name; Policy = $policy }
        $state | ConvertTo-Json -Depth 8 | Set-Content -Path $statePath -Encoding UTF8
        $trackedContainers += $name
        $newPolicy = if ($name -eq "rpm-edge-proxy") { "always" } else { "unless-stopped" }
        & docker update --restart $newPolicy $name | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Cannot set the restart policy on container '$name'." }
    }
} else {
    Write-Warning "Docker is not currently available. The watchdog will try to start Docker Desktop after logon."
}

$dockerDesktopCandidates = @(
    (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
    (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
)
$dockerDesktopPath = $dockerDesktopCandidates | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1

$watchdogConfig = [ordered]@{
    Version = 1
    ProjectPath = $ProjectPath
    ComposePath = $composePath
    Containers = @($trackedContainers | Sort-Object -Unique)
    RebootAfterFailures = $RebootAfterFailures
    MinimumUptimeBeforeRebootMinutes = 20
    MinimumHoursBetweenAutomaticReboots = 6
    DockerDesktopPath = $dockerDesktopPath
    HealthUrl = "http://127.0.0.1:9090/healthz"
}
$watchdogConfig | ConvertTo-Json -Depth 6 | Set-Content -Path $watchdogConfigPath -Encoding UTF8
Copy-Item -Path (Join-Path $PSScriptRoot "windows-server-watchdog.ps1") `
    -Destination $installedWatchdogPath -Force

$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$installedWatchdogPath`" -ConfigPath `"$watchdogConfigPath`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArguments
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $WatchdogIntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $taskPrincipal -Description "Keeps Docker and server containers running; guarded recovery reboot." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName

Write-Host ""
Write-Host "Windows server mode is configured." -ForegroundColor Green
Write-Host "Baseline and logs: $dataDir"
Write-Host "Tracked containers: $((@($watchdogConfig.Containers) -join ', '))"
if ($RebootAfterFailures -eq 0) {
    Write-Host "Automatic recovery reboot is disabled."
} else {
    Write-Host "Automatic reboot occurs after $RebootAfterFailures consecutive failed checks, with a six-hour reboot-loop guard."
}
Write-Warning "Enable 'Power on AC attach/after power loss' in BIOS/UEFI; Windows cannot set this portably."
Write-Warning "Docker Desktop starts after a user logs on. For unattended recovery, configure a physically secured server account with Microsoft Sysinternals Autologon."
Write-Warning "Updates now download and wait for operator installation. Schedule regular maintenance so security updates are still applied."
Write-Warning "In Docker Desktop Settings, turn off automatic component updates and apply Docker updates only during maintenance windows."
Write-Host "Perform one controlled Windows restart, then verify OSCAR, the camera, and the RPM feed."
Write-Host "Run scripts\restore-windows-server.ps1 to restore the saved Windows settings."
