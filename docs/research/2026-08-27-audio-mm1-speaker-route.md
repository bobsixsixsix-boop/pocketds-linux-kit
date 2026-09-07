# Pocket DS MM1 speaker route verification

Date: 2026-08-27 (Asia/Tokyo)

Scope: PDS-018 speaker playback only. Microphone, DisplayPort, reboot, and resume
remain separate verification items.

## Observed failure mechanism

On the current SM8550 card topology, routing the speakers through MultiMedia2
also reaches the DisplayPort backend. With no valid DisplayPort ELD, capability
probing can reject `hw_params` and leave PipeWire without a real speaker sink.

The field fix separates the paths:

- Speaker: MultiMedia1, `hw:0,0`, WSA backend enabled, DP-on-MM1 disabled.
- DisplayPort: MultiMedia2, `hw:0,1`, enabled only by its UCM device sequence.

## Real-device evidence

- `alsaucm -c hw:0 dump text` resolved Speaker to `hw:SM8550APS,0` and
  DisplayPort to `hw:SM8550APS,1`.
- `aplay -l` exposed MultiMedia1 playback as device 0 and MultiMedia2 as device 1.
- PipeWire exposed a real `alsa_output.platform-sound.HiFi__Speaker__sink` backed
  by `api.alsa.path=hw:SM8550APS,0`; there was no `auto_null` sink.
- A short low-volume `paplay` test completed with exit status 0. The sink moved
  to `IDLE`, and no new PipeWire/WirePlumber `hw_params`, ELD, XRUN, abort, or
  auto-null error was logged.

## Remaining verification

- PipeWire currently exposes no microphone source; capture routing is not fixed
  or claimed by this change.
- DisplayPort playback needs a real connected sink and must not mute speakers
  permanently after disconnect.
- Reboot and deep-resume recovery are not covered by the short playback test.
