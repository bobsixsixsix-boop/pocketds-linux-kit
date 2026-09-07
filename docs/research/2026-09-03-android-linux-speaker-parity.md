# Pocket DS Android/Linux speaker parity — 2026-09-03

## Evidence boundary

The installed Linux UCM already uses the validated MultiMedia1 speaker path and
high WSA amplifier gain.  Android's vendor path additionally exposes Qualcomm
compression, sensing and protection controls.  Those controls depend on vendor
DSP/calibration state and are not safe to toggle blindly from Linux.

The parity layer therefore sits above the working UCM route.  It creates one
PipeWire virtual sink whose playback stream is pinned to
`alsa_output.platform-sound.HiFi__Speaker__sink`.  Bluetooth, DisplayPort,
headset and microphone nodes do not enter the graph.

## Tuning

- 68 Hz high-pass rejects content below the tiny speaker's useful range.
- A conservative 150 Hz shelf and 310 Hz bell add warmth and body.
- Small cuts at 850 Hz, 3.2 kHz and above 7 kHz reduce boxiness and thin edge.
- Reserved headroom feeds a bounded cubic soft limiter
  (`y = 1.5x - 0.5x^3`) and a 0.96 output ceiling.
- The controller splits volume between a 70% backing sink and the visible
  virtual sink.  Activation and deactivation preserve the effective volume and
  mute state, avoiding an unexpected jump.

This is a conservative first acoustic profile, not a measurement-derived claim
of exact Android frequency response.  Audible A/B verification on the physical
device remains the authority for later tuning.

## Rollback

Stop and disable `pocketds-speaker-enhancement.service`.  Its stop action moves
the default back to the validated physical speaker sink and restores the
effective volume; the linked filter-chain service then leaves the session and
removes the virtual sink.
