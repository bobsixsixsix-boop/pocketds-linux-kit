# PDS-013 phone-integration decision refresh

Date: 2026-08-28. This is a source and read-only device-state review; no
package, firewall, VPN, pairing or account state was changed.

## Upstream evidence

- KDE Connect documents local file/link sharing, phone notifications, remote
  commands and Android/iOS clients. It is already native to the Pocket DS KDE
  session: <https://kdeconnect.kde.org/> and
  <https://kdeconnect.kde.org/download.html>.
- The LocalSend protocol uses UDP multicast discovery and TCP/HTTPS transfer on
  port 53317 by default: <https://github.com/localsend/protocol/blob/main/README.md>.
- LocalSend's official security advisory says unauthenticated UDP discovery can
  be spoofed to impersonate a peer and intercept or modify transfers. The
  advisory lists affected versions through 1.17.0 and no patched version:
  <https://github.com/localsend/localsend/security/advisories/GHSA-424h-5f6m-x63f>.
  Transport HTTPS does not authenticate the discovery identity, so simply
  opening the port only on a trusted LAN is not a complete mitigation.
- Tailscale's official Linux path installs a system daemon, uses `/dev/net/tun`
  in normal Linux mode and requires explicit tailnet authentication:
  <https://tailscale.com/docs/install/linux> and
  <https://tailscale.com/docs/concepts/userspace-networking>.

## Read-only Pocket DS facts

On the current Fedora 44 device:

- `kde-connect-26.08.0-1.fc44.aarch64` and
  `kdeconnectd-26.08.0-1.fc44.aarch64` are installed.
- the KDE Connect user daemon is `active/running`;
- firewalld is running;
- `pocketds-home` permits the stock `kdeconnect` service, which resolves to
  1714-1764/tcp and 1714-1764/udp;
- `public` does not permit `kdeconnect`;
- neither LocalSend nor Tailscale is installed.

No SSID, connection UUID, IP address, peer identity or credential was recorded.

These facts are now reproducible with `make observe-phone-integration`. Its
fixed command allowlist cannot enumerate NetworkManager identities or KDE
Connect peers, cannot mutate network/account state, bounds every probe to five
seconds and 4096 bytes, and emits only booleans plus the explicit `not_run`
physical-test state. `make test-phone-integration` validates those constraints
entirely with fixtures.

## Decision and remaining acceptance

KDE Connect remains the only firmware-default phone integration path, and only
on a NetworkManager connection the user explicitly binds to `pocketds-home`.
LocalSend is held outside the firmware until an upstream patched release is
identified and its discovery/peer-binding behavior passes an adversarial test.
Tailscale remains an opt-in remote-access layer; the firmware must never install
it, enroll a node or persist tailnet identity without explicit user action.

PDS-013 remains open until a phone is physically present for pairing, bidirectional
file transfer, link sharing, disconnect/reconnect and suspend/resume reconnection.

## 2026-08-29 security recheck

The hold remains correct. LocalSend's own current GHSA still marks versions
through `1.17.0` affected and lists **no patched version** for discovery peer
impersonation. A separate official advisory published in 2026 also marks
versions through `1.17.0` affected with no patched version for stored XSS in
the Web Share filename renderer:

- <https://github.com/localsend/localsend/security/advisories/GHSA-424h-5f6m-x63f>
- <https://github.com/localsend/localsend/security/advisories/GHSA-34v6-52hh-x4r4>

NVD's prose for CVE-2025-54792 says 1.17.0 fixed the issue, but its later CNA
change record and the vendor advisory identify `<= 1.17.0` as affected. This
conflict must fail closed: a downstream version number or the presence of one
related patch cannot substitute for an upstream patched-version declaration.
The firmware therefore continues to install neither LocalSend nor its 53317
firewall opening. KDE Connect on the explicitly trusted zone remains the
default; Tailscale remains user-opt-in.
