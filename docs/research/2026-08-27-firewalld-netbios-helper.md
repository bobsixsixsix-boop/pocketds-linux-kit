# Firewalld failure on the Pocket DS kernel

Date: 2026-08-27 (Asia/Tokyo)

Issue: PDS-021.

## Root cause

Firewalld 2.4.4 remained active as a process but reported state `failed` with
return code 251. Its stock FedoraWorkstation zone includes `samba-client`, which
includes the `netbios-ns` helper. The Pocket DS kernel explicitly lacks
`CONFIG_NF_CONNTRACK_NETBIOS_NS`; an nftables check for that helper returned
`ENOENT`. Firewalld then discarded the user configuration and loaded its stock
failsafe rules.

This explains why KDE Connect could listen on TCP/UDP 1716 while inbound
discovery remained blocked. The XML configuration itself passed
`firewall-offline-cmd --check-config`.

## User-space compatibility policy

- Default to the stock `public` zone for unknown networks. It retains SSH,
  mDNS, and DHCPv6 client traffic but does not request the missing helper.
- Install a minimal `pocketds-home` zone that adds only the stock `kdeconnect`
  service (TCP/UDP 1714-1764) to the public baseline.
- Never add KDE Connect to public.
- Never store a trusted network's SSID, UUID, profile, or credential in Git.
  Trust is an explicit per-machine NetworkManager binding.

## Verification in this batch

- Firewalld reports `running` rather than active-but-failed.
- Default zone is public.
- The active trusted interface is in `pocketds-home` on the development device.
- `pocketds-home` queries yes for KDE Connect; public queries no.
- nftables contains KDE Connect ports and no NetBIOS helper.
- KDE Connect remains listening, and ordinary DNS/HTTPS connectivity succeeds.
- No phone was paired, so discovery, transfer, notification, and resume
  reconnection remain separate integration tests.

The final firmware kernel should still enable the helper or document deliberate
incompatibility, then audit every firewalld stock service against its netfilter
configuration. The user-space zone is a safe daily-use workaround, not proof of
full Fedora firewall compatibility.
