# PDS-018B physical microphone experiment

This directory is an opt-in research overlay. It is not a supported audio
configuration and must never be referenced by `scripts/install.sh`,
`scripts/import-live.sh`, `make install`, or `make install-all`.

The complete UCM pair is based on commit
`88d307fd727ca073b3249f63a9d451eacfc887be`. It preserves the machine-verified
MM1 speaker boolean pair `1,0`. `HiFi.conf` adds a tentative two-channel `Mic`
device using the Qualcomm VA-macro DMIC0 and DMIC1 enable/disable sequences and
capture PCM `hw:${CardId},2`.

The route is not a guess about a missing kernel driver. The locked Pocket DS
kernel DTS already declares `VA_CODEC_DMA_TX_0`, routes `VA DMIC0` and
`VA DMIC1` to `vdd-micb`, selects both DMIC pin groups and provides the VA
macro supply. The live kernel enumerates MultiMedia3/4 capture PCMs and all
DMIC0/1 controls. The supported HiFi UCM simply has no `Mic` device, so
PipeWire cannot expose the physical source. Exact source, sequence and live
baseline identities are pinned in `manifest.json` and checked by the silent
audio acceptance gate.

There is intentionally no installer or apply helper. Before staging this in a
throw-away image, verify every base and overlay hash in `manifest.json`. Then
run the silent acceptance tool with both:

```text
--repo-ucm-dir experiments/audio/physical-mic --expect-physical-mic
```

The experimental gate rejects a `.monitor` source. Recording, route switching,
acoustic/channel validation and suspend/resume validation remain separate,
supervised procedures with a rollback snapshot.

The overlay must not be staged while another long-running acceptance run is in
progress. Its first live activation needs an attended rollback window. Mixer
state may survive in `/var/lib/alsa/asound.state`; current values are diagnostic
evidence, not proof that a microphone stream works and not permission to clear
or rewrite that state.
