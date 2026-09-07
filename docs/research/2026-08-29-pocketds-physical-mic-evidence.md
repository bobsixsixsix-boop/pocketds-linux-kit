# Pocket DS physical microphone evidence

Date: 2026-08-29 (Asia/Tokyo)

This note records read-only facts needed by PDS-018. It is not permission to
switch the UCM profile or record audio.

## Current live state

- The running card is `SM8550-APS` (`alsa_card.platform-sound`).
- ALSA enumerates `MultiMedia3 Capture` at card 0/device 2 and
  `MultiMedia4 Capture` at card 0/device 3.
- PipeWire exposes the built-in speaker sink but no physical source.
- `pactl get-default-source` therefore resolves to the speaker monitor:
  `alsa_output.platform-sound.HiFi__Speaker__sink.monitor`.
- The ASR keyboard correctly refuses this source rather than recording speaker
  playback as microphone input.

The missing PipeWire source is a userspace routing gap, not evidence that the
kernel lacks capture PCMs. No stream or acoustic recording has been run.

## Configuration provenance

The latest official
[alsa-project/alsa-ucm-conf](https://github.com/alsa-project/alsa-ucm-conf)
tree was rechecked at commit
`00175aa645c482111d096c3d8230f182a875d286`. It contains the SM8550 HDK
profile but no APS or Pocket DS directory. There is therefore no current
upstream Pocket DS microphone profile that can be adopted unchanged.

The live APS files are owned by the downstream Fedora COPR package
`pocketds-userspace-20260507-20260730174706.fc44.noarch`, whose metadata points
to the ROCKNIX distribution and the linux-pocketds maintainers. The existing
speaker fixes intentionally make those two files differ from their packaged
bytes; PDS-018 binds the exact accepted live preimages instead of treating RPM
verification drift as permission to overwrite them.

The experimental Mic device is a minimal delta over those accepted APS files.
It uses the official Qualcomm VA-macro `DMIC0`/`DMIC1` enable and disable
sequences and capture PCM `hw:${CardId},2`. The device tree, ALSA controls and
sequence hashes are bound in `experiments/audio/physical-mic/manifest.json`.

## Native audio graph dependency

Fedora `pipewire-utils-1.6.8-1.fc44.aarch64` is installed. It adds only the
official `/usr/bin/pw-dump` tool needed for a bounded native graph audit; its
installation did not restart PipeWire or alter the live graph.

The current socket contract is:

- `pipewire.socket`: exactly two stream listeners,
  `/run/user/1000/pipewire-0` and `pipewire-0-manager`;
- `pipewire-pulse.socket`: exactly one stream listener,
  `/run/user/1000/pulse/native`;
- PipeWire holds two matching listening descriptors, PipeWire Pulse holds one,
  and WirePlumber holds none;
- `/proc/net/unix` also contains normal state-03 connected rows for the same
  pathnames, so the attestor must distinguish them from the unique state-01
  listener rows rather than rejecting them.

## Deployment and acceptance gate

Before a live UCM change, PDS-018 still has to pass a new independent audit
covering exact listener cardinality, fixed uid-1000 client execution, no
cross-boot pathname downgrade, dependency checks before durable intent, and a
rollback that never restores a vanished Mic default source.

After that audit, the first activation remains attended and reversible. A
successful source enumeration is not acoustic proof. Three explicitly
confirmed, bounded, memory-only clips must establish signal level, channel
behaviour and cleanup before the hidden ASR credential is provisioned and real
short/long/silence/offline recognition is evaluated.
