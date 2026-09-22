# Windows 11 Unattended Server Mode

This deployment can make the OSCAR Windows 11 laptop behave like an always-on appliance while preserving a reversible baseline of the settings it changes.

## What the server-mode script changes

- Creates and activates a dedicated `RPM Server Always On` power plan.
- Disables automatic sleep, idle hibernation, lid-close sleep, disk idle shutdown, USB selective suspend, and network-adapter selective suspend on both AC and battery.
- Keeps the processor at full availability on AC and disables Windows background power throttling and user-inactivity QoS reduction.
- Disables Fast Startup and enables automatic reboot after a Windows system crash.
- Disables Edge sleeping tabs and Edge/Chrome efficiency modes while keeping browser background mode enabled.
- Leaves Windows Update enabled, but changes it to download updates and wait for an operator to install them instead of installing unexpectedly.
- Gives every currently running Docker container a restart policy. The RPM proxy uses `always`; other running containers use `unless-stopped`.
- Installs an interactive-user watchdog that runs every two minutes after sign-in, starts Docker Desktop, recreates the RPM Compose service, restarts tracked containers, checks `/healthz`, and performs a guarded Windows reboot only after repeated failed recovery attempts.
- Saves the original power, registry, adapter, and container-restart settings under `C:\ProgramData\RPMEdgeProxy` for restoration.
- Copies the runtime Compose files and application into `C:\ProgramData\RPMEdgeProxy\app`, so later moving or deleting the downloaded source folder does not break recovery.

The watchdog never reboots because the physical RPM at `192.168.2.3` is unavailable. It checks the local proxy process health, not upstream readiness.

## One-time setup

First install all currently pending Windows updates, perform a controlled reboot, deploy the proxy and OSCAR containers, and confirm they work. Run the next step locally because changing network-adapter power management briefly restarts that adapter. Then open PowerShell as Administrator:

```powershell
cd C:\path\to\rpm-edge-proxy
.\scripts\configure-windows-server.ps1 `
    -InterfaceAlias "Ethernet" `
    -RebootAfterFailures 8
```

Perform one more controlled Windows restart so the browser, update, Fast Startup, and power-throttling policies are fully applied. Confirm the machine signs in, Docker Desktop starts, all containers return, OSCAR reaches camera `192.168.3.2`, and the RPM feed is available through `192.168.2.2:1600`.

With the default two-minute interval, eight consecutive failures allow roughly sixteen minutes for Docker recovery before a reboot is considered. The watchdog will not reboot during the first twenty minutes after boot and permits at most one automatic recovery reboot every six hours.

Use `-RebootAfterFailures 0` to configure all always-on behavior without allowing watchdog-initiated Windows reboots.

## Required firmware and sign-in settings

Software cannot turn on a laptop that has completely lost power. In BIOS/UEFI, enable the vendor setting named something like:

- `Power On AC Attach`
- `AC Recovery`
- `After Power Loss: Power On`
- `Wake on AC`

Docker Desktop starts in a user session. For recovery without a person entering a password, use a dedicated, physically secured local server account and configure automatic sign-in with [Microsoft Sysinternals Autologon](https://learn.microsoft.com/en-us/sysinternals/downloads/autologon). Autologon stores the credential as an LSA secret, but a local administrator can still recover it. Do not enable automatic sign-in on an unsecured laptop.

Also open Docker Desktop Settings and:

1. Confirm Linux containers are selected.
2. Turn off automatic component updates.
3. Apply Docker Desktop updates manually during maintenance windows.

The watchdog starts Docker Desktop after logon, so Docker Desktop's own “start when you sign in” option may remain enabled as an additional safeguard.

## Maintenance mode

Pause automatic container recovery and reboot before planned maintenance:

```powershell
New-Item C:\ProgramData\RPMEdgeProxy\watchdog.pause -ItemType File -Force
```

Resume afterward:

```powershell
Remove-Item C:\ProgramData\RPMEdgeProxy\watchdog.pause
Start-ScheduledTask -TaskName "RPM Edge Proxy Server Watchdog"
```

Watchdog activity is recorded in `C:\ProgramData\RPMEdgeProxy\watchdog.log`. To cancel a pending 60-second watchdog reboot:

```powershell
shutdown.exe /a
```

## Updates and thermal protection

Server mode prevents unattended Windows Update installation, but it does not disable security updates. Schedule a monthly maintenance window, pause the watchdog, install Windows and Docker updates, reboot, verify OSCAR and the RPM feed, then resume the watchdog.

The script disables software power-saving throttles. It cannot and should not disable CPU thermal protection. Keep the laptop ventilated, use its rated power supply, and monitor battery health.

Domain policy, mobile-device management, or Windows Home limitations can override local Windows Update and browser policy settings. Verify the effective configuration after every policy change.

## Restore the original settings

From an elevated PowerShell window:

```powershell
.\scripts\restore-windows-server.ps1
Restart-Computer
```

This removes the watchdog, restores the original power plan, registry settings, adapter power controls, and saved container restart policies. It does not undo the BIOS/UEFI AC-recovery setting or Sysinternals Autologon; disable those separately if they were enabled.
