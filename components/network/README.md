# Pocket DS trusted network zone

The firmware installs `pocketds-home` but never guesses which Wi-Fi network is
trusted. Unknown and newly added connections use the `public` default zone,
which does not expose KDE Connect.

After explicitly deciding that the currently connected Wi-Fi is trusted, bind
that NetworkManager connection without recording its name in the repository:

```bash
iface=$(nmcli -t -f DEVICE,TYPE,STATE device status |
    awk -F: '$2 == "wifi" && $3 == "connected" { print $1; exit }')
uuid=$(nmcli -g GENERAL.CON-UUID device show "$iface")
sudo nmcli connection modify uuid "$uuid" connection.zone pocketds-home
sudo systemctl restart firewalld
```

Verify that `pocketds-home` contains `kdeconnect`, `public` does not, and the
interface is assigned to the trusted zone. SSIDs, UUIDs, connection profiles,
and credentials are machine-private data and must not be committed or included
in diagnostic bundles.

The repeatable firmware-side preflight does not enumerate that interface, any
network identity or KDE Connect peers:

```bash
make observe-phone-integration
```

It checks only fixed package, daemon, zone and service-port facts. A passing
preflight is not a substitute for the physical phone pairing/transfer/resume
acceptance run.

LocalSend is intentionally not installed or opened by this firmware while its
upstream CVE-2025-54792/GHSA-424h-5f6m-x63f advisory lists no patched release.
Tailscale is also not installed, authenticated, or enabled by default because
it creates a persistent daemon, TUN interface and account-bound device state.
See `docs/research/2026-08-28-pds013-phone-integration.md`.
