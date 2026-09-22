# Offline Image Build and Installation

The offline bundle contains the RPM Edge Proxy Linux-container image for the destination CPU architecture. It does not contain Windows, Raspberry Pi OS, Docker Desktop, Docker Engine, or operating-system packages.

## 1. Prepare an internet-connected builder

Install Docker Engine or Docker Desktop with Docker Buildx. Docker Desktop must be using Linux containers. The builder can be Windows with WSL/Git Bash, macOS, or Linux.

Confirm Buildx is available:

```bash
docker buildx version
```

## 2. Build both offline images

From the project root:

```bash
./scripts/build-offline-bundles.sh 2.0.0
```

On Windows, run that command from WSL or Git Bash. The script builds each architecture separately and creates:

| File | Destination |
| --- | --- |
| `dist/rpm-edge-proxy-2.0.0-linux-amd64.tar.gz` | Windows 11 Docker Desktop on a normal Intel/AMD laptop |
| `dist/rpm-edge-proxy-2.0.0-linux-arm64.tar.gz` | 64-bit Raspberry Pi OS on ARM64 |
| `dist/SHA256SUMS` | Checksums for transfer verification |

Do not rename the image archives. The installers look for the versioned names shown above.

## 3. Verify and transfer

On Linux:

```bash
cd dist
sha256sum --check SHA256SUMS
```

On macOS:

```bash
cd dist
shasum -a 256 --check SHA256SUMS
```

On Windows PowerShell, display the calculated hash and compare it with the corresponding line in `SHA256SUMS`:

```powershell
Get-FileHash .\dist\rpm-edge-proxy-2.0.0-linux-amd64.tar.gz -Algorithm SHA256
Get-Content .\dist\SHA256SUMS
```

Copy the complete `rpm-edge-proxy` directory to the offline machine, including `compose.yaml`, `config`, `scripts`, and `dist`. Recheck the checksum after copying. A USB drive is sufficient; no registry or image server is required.

## 4A. Install on offline Windows 11

Prerequisites that must already be installed:

- Docker Desktop configured for Linux containers;
- an Ethernet adapter connected to the RPM network; and
- PowerShell administrator access.

Open PowerShell as Administrator in the copied project directory. Load the AMD64 image:

```powershell
docker load --input .\dist\rpm-edge-proxy-2.0.0-linux-amd64.tar.gz
docker image inspect rpm-edge-proxy:2.0.0
```

If the Ethernet adapter already has `192.168.2.2/24`:

```powershell
.\scripts\install-windows.ps1 -InterfaceAlias "Ethernet" -NoBuild
```

If the installer should add `192.168.2.2/24` after checking for a conflict:

```powershell
.\scripts\install-windows.ps1 `
    -InterfaceAlias "Ethernet" `
    -ConfigureNetwork `
    -NoBuild
```

The existing static camera address `192.168.3.99/24` remains on the adapter. Verify the proxy:

```powershell
.\scripts\verify-windows.ps1 -InterfaceAlias "Ethernet"
```

For unattended operation, follow [WINDOWS-SERVER.md](WINDOWS-SERVER.md) and then run:

```powershell
.\scripts\configure-windows-server.ps1 -InterfaceAlias "Ethernet"
```

## 4B. Install on an offline Raspberry Pi

Prerequisites that must already be installed:

- Raspberry Pi OS Lite 64-bit;
- Docker Engine with Compose v2;
- NetworkManager/`nmcli`;
- `iproute2`; and
- `iputils-arping`.

Copy the complete project directory, including the ARM64 archive under `dist`, to the Pi. From a local keyboard/display, run:

```bash
cd /path/to/rpm-edge-proxy
sudo ./scripts/install-pi.sh --apply-network --interface eth0
```

The installer detects and loads `dist/rpm-edge-proxy-2.0.0-linux-arm64.tar.gz`, so it does not need an image registry. It then assigns `192.168.2.2/24`, starts the container, and verifies the service. Reboot once and verify again:

```bash
sudo /opt/rpm-edge-proxy/scripts/verify.sh eth0
```

## Troubleshooting

- `pull access denied` or an attempted registry connection means the required image tag was not loaded. Check `docker image inspect rpm-edge-proxy:2.0.0`.
- `no matching manifest` or `exec format error` usually means the wrong CPU archive was loaded. Use AMD64 for the Windows laptop and ARM64 for the Pi.
- A bind error for `192.168.2.2:1600` means the host does not own `.2`, another process already uses port `1600`, or another device conflicts with that address.
- `/readyz` returns HTTP 503 until the proxy can connect to the real RPM at `192.168.2.3:1600`; this does not mean the container image failed to load.
