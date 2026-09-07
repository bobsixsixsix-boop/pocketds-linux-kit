# PDS-018 audio audit — 2026-08-28

Read-only inspection found PipeWire/WirePlumber healthy and the MM1 speaker
sink present. The repository and live UCM hashes already match the MM1 split;
an earlier claim that Git would restore MM2 was disproved against the
authoritative repository.

Confirmed remaining issues:

- The WSA frontend control advertises two booleans, but direct tests of `0,1`
  and `1,1` left its second member off. It is not evidence of a dead right
  speaker. Source now records the machine-valid `1,0` route explicitly; PCM is
  still two-channel and audible L/R verification remains pending.
- HiFi exposes no physical capture source. Pulse compatibility currently falls
  back to the speaker monitor, which must never be treated as a microphone.
- The keyboard previously mutated numeric ALSA controls whose meanings drifted;
  one current numid addresses DisplayPort. Source now removes all numid writes,
  rejects missing/monitor default sources and records through PipeWire.
- Kernel/DT/ALSA capability is no longer speculative: the locked Pocket DS DTS
  has the VA capture DAI, `VA DMIC0/1` routes, both DMIC pin groups and mic-bias
  supply; the live card exposes MultiMedia3/4 capture and all required VA
  DMIC0/1 controls. The missing layer is the supported HiFi UCM `Mic` device.
  The overlay remains an experiment until supervised capture validates device
  identity, channel map, gain, clipping, privacy cleanup and resume.
- DisplayPort is physically disconnected, so ELD, route switching and
  `JackHWMute` behavior are not yet verified.

Next live gates: silent service/source/mixer inspection; safe-volume left/right
speaker test; explicit microphone capture consent; DP hotplug/unplug 20 cycles;
then boot and resume repetitions. None may silently change volume, profile or
route, and cancellation must remove temporary audio and child processes.

## Complete-matrix evidence boundary

The repository now has `pds018-audio-matrix-evaluate.py`, an offline evaluator
for the future supervised ledger. It cannot play or record audio, call mixer or
route tools, restart services, reboot, suspend or create the evidence it judges.
That separation prevents a default test from unexpectedly making sound,
capturing private speech or changing machine state.

The project acceptance policy is explicit rather than inferred from a single
successful stream:

| Case | Minimum supervised attempts | Additional gate |
|---|---:|---|
| left speaker | 1 | human observer confirms the intended side |
| right speaker | 1 | human observer confirms the intended side |
| stereo | 1 | human observer confirms both-channel content |
| physical microphone | 3 | explicit consent, never a monitor source, no clipping, all temporary captures removed |
| DisplayPort hotplug | 20 | DP sink appears and speaker recovers after every disconnect |
| cold boot | 5 | speaker route recovers |
| deep resume | 10 | speaker route recovers |

Every case additionally requires all attempts to pass, zero PipeWire and
WirePlumber restart delta, zero auto-null observation, XRUN, route mismatch or
cancellation. The evidence root binds the exact 40-character repository
revision plus SHA-256 of `HiFi.conf` and `SM8550-APS.conf`; evaluation against a
different source tree is always incomplete. Input is current-user private,
single-link, `O_NOFOLLOW`, nonempty and no larger than 256 KiB. Duplicate JSON
keys, non-finite values, unknown cases/fields and inconsistent outcome totals
fail closed. A persistent report is a new 0600 file and is never overwritten.

The aggregate report contains counts, gates and public source hashes only. It
emits no audio, transcript, timestamp, filesystem path or device identifier.
Run it only after a separate supervised harness has produced the private
ledger:

```sh
make evaluate-audio-matrix \
  EVIDENCE=/path/to/private-audio-matrix.json \
  OUTPUT=/path/to/new-audio-matrix-report.json
```

Twelve fixture/static tests pass. No real ledger exists yet, so this work does
not change PDS-018 from VERIFICATION or claim audible, microphone, DP, boot or
resume success.

Source commits are local `d37e310` and device `8c56c06`. The 12 targeted tests
and full `make test` passed on both hosts. After device verification the active
24-hour telemetry soak remained PID 162791 with zero restarts. No audio matrix
ledger was synthesized or evaluated during this source-only step.

## Physical capture root cause and locked preflight

A second read-only audit bound the conclusion to exact upstream identities:

- linux-pocketds `pocketds/v7.1-rc2` remains
  `a4975ce7c8eec079bd0cf7ec3890e100e016813f`. Its Pocket common DTS SHA-256 is
  `350cacc7…`; `sound` uses `VA_CODEC_DMA_TX_0`, routes `VA DMIC0` and
  `VA DMIC1` to `vdd-micb`, and `lpass_vamacro` selects `dmic01_default`,
  `dmic23_default` and the 1.8 V mic-bias supply.
- The running kernel is the locked `7.1.0-100.20260730172655.pocketds.fc44`
  build. `/proc/asound/pcm` and `arecord -l` expose device 2 MultiMedia3 and
  device 3 MultiMedia4 capture. The exact VA frontend, decimator, DMIC mux,
  AIF1 capture mixer and volume controls needed for DMIC0/1 all exist.
- ALSA UCM upstream commit `00175aa645c482111d096c3d8230f182a875d286`
  uses the same DMIC0 plus DMIC1 sequences for two-channel internal microphones
  on current Qualcomm profiles. The installed Fedora `alsa-ucm
  1.2.16.1-1.fc44` copies of all four sequences match upstream byte-for-byte.
- The live HiFi profile still has zero physical PipeWire sources. This is
  consistent with its missing `SectionDevice."Mic"`; it does not indicate a
  missing kernel driver.

`experiments/audio/physical-mic/manifest.json` now locks the kernel source/DTS,
live release, upstream UCM commit, four sequence hashes, capture PCM and
observed boundary. The overlay follows the upstream two-decimator form instead
of claiming a two-channel device from DMIC0 alone. The silent acceptance tool
now checks the kernel/card/PCM/package and ten named controls without opening a
stream. Current route values are reported only as read-only diagnostics because
ALSA state can persist; they are not treated as acoustic success.

Eleven focused tests and lint pass locally. No UCM file was installed, no
profile/service was changed, no mixer was written and no audio was captured.
Deployment and three consented recordings remain post-soak, attended gates.

Primary sources:

- <https://gitlab.com/linux-pocketds/linux/-/commit/a4975ce7c8eec079bd0cf7ec3890e100e016813f>
- <https://github.com/alsa-project/alsa-ucm-conf/tree/00175aa645c482111d096c3d8230f182a875d286/ucm2/codecs/qcom-lpass/va-macro>
- <https://github.com/alsa-project/alsa-ucm-conf/blob/00175aa645c482111d096c3d8230f182a875d286/ucm2/Qualcomm/x1e80100/HiFi.conf>
