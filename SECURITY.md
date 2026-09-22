# Production Security Checklist

- Keep the RPM LAN isolated from untrusted networks and the internet.
- Ensure exactly one host owns `192.168.2.2`; duplicate ownership causes intermittent misdelivery as ARP caches change.
- Restrict TCP `1600` to authorized receiving computers with the host firewall. The supplied Windows rule allows only `LocalSubnet`; narrow it to exact IP addresses when known.
- Keep the status service bound to host loopback (`127.0.0.1:9090`).
- Keep `client_writes` set to `discard` unless the RPM protocol owner explicitly approves multiple clients sharing a command channel.
- Protect Pi administration with SSH keys and an authorized management path.
- Keep the container read-only, unprivileged, capability-free, and resource-limited as supplied.
- Review and scan the pinned base image before a controlled production release.
- Verify SHA-256 checksums before loading offline images.
- Use wired Ethernet, stable power, and a tested high-endurance system disk.
- Apply operating-system, Docker, and image updates during planned maintenance, then repeat all failure tests in the runbook.
- Treat the Pi or Windows host as a single point of failure. High availability requires a second independently powered proxy and a controlled floating-IP design; never simply assign `.2` to both.

The proxy does not originate internet traffic, terminate encryption, store credentials or RPM data, interpret events, or proxy camera traffic.
