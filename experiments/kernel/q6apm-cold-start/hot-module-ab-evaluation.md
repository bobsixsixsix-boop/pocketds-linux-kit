# q6apm hot-module A/B evaluation

Status: **NO-GO on the Pocket DS**. This is an evidence-only assessment; no
module was unloaded, inserted, copied to the device, or tested live.

## What is technically available

The pinned baseline config enables normal module removal and does not enforce
module signatures:

- `CONFIG_MODULE_UNLOAD=y`;
- `CONFIG_MODULE_FORCE_UNLOAD` is disabled;
- `CONFIG_MODULE_SIG` is disabled;
- baseline `snd-q6apm.ko` vermagic is
  `7.1.0-100.20260730172655.pocketds.fc44.aarch64 SMP preempt mod_unload aarch64`.

The module has four metadata-visible reverse consumers: `q6apm-dai.ko`,
`q6apm-lpass-dais.ko`, `q6prm.ko`, and, through `q6prm.ko`,
`q6prm-clocks.ko`. The live read-only observation strengthens that dependency
evidence: `snd_q6apm` had refcount 4 with holders `q6apm_dai`, `q6prm`, and
`q6apm_lpass_dais`; PipeWire, PulseAudio compatibility, and WirePlumber were
all active.

The core driver does have a GPR `.remove` callback which depopulates its child
devices and unregisters the ASoC component. That makes source-level removal
possible, but does not prove that the complete bound sound card and every DSP
graph can be torn down and rebound without a hang or a stale asynchronous
callback on this hardware.

## Why the proposed `/run` replacement is not fail-closed

The persistent candidate intentionally has a distinct kernel release, so its
`snd-q6apm.ko` has different vermagic and cannot be inserted into the running
baseline kernel. A separate development-only module would have to be rebuilt
with the baseline `KERNELRELEASE`, baseline config, compiler, build salt, and
`Module.symvers`. Rewriting vermagic is not an acceptable substitute.

Even with such a module, `rmmod snd_q6apm` cannot be the first operation. All
four reverse consumers and all open ALSA/PipeWire users would have to release
the graph first. Unloading that stack unregisters and later recreates core ASoC
components; failure can occur after some modules are gone but before the core
is replaceable. Loading the disk copy again would not prove that the DSP,
topology, PCM devices, WirePlumber policy, and every client return to the same
state. Forced unload is unavailable and would be unsafe here anyway.

Because the bug under test is itself a DSP graph-start timeout, a teardown or
rebind can block in the same control path. There is no independently verified
bounded rollback that guarantees recovery without a reboot. Stopping user
audio narrows the race but does not eliminate kernel/GPR/firmware state.

## Decision

Do not offer or run the hot swap on the user's device. Use the separately
versioned full-kernel candidate and the existing recovery-boot design for the
eventual supervised A/B. Keep UCM, DMIC routing, PipeWire/WirePlumber, and the
rest of the kernel fixed during that experiment.
