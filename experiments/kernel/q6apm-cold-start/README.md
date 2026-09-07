# PDS ASR q6apm cold-start candidate

This directory preserves two unmodified upstream ASoC fixes and the recipe and
verification tools for a built, default-disabled Pocket DS kernel candidate.
The candidate has not been installed, booted, copied to the device, or runtime
authorized by any project target.

## Source and patch order

The pinned downstream source is
`linux-pocketds:pocketds/v7.1-rc2` at
`a4975ce7c8eec079bd0cf7ec3890e100e016813f`.
Apply the patches in this exact order:

1. upstream `3075ae5abbc370d2a9a01bd6d554a412d406f5bd` propagates
   `audioreach_set_media_format()` failures to callers;
2. upstream `daa7ffd765ae67a83e77dae32c66ce2d6d995d19` increments the
   graph start count only after the DSP accepts `APM_CMD_GRAPH_START`, and
   prevents stop-count underflow after a failed start.

The ordered pair was first applied to a temporary Git index populated from the
pinned commit without altering a checkout. It was then applied by the source
RPM `%prep` stage with `patch --batch --fuzz=0 --reject-file=-`. Two isolated
Fedora 44 aarch64 roots built the complete package successfully.

## Diagnostic boundary

These fixes match the observed failure class, but have a deliberately narrow
claim. `3075ae5` propagates a media-format programming error that the old
driver hides. `daa7ffd` prevents a failed `APM_CMD_GRAPH_START` from poisoning
`start_count`, so a later retry is not incorrectly skipped. Neither patch
guarantees that the *first* `APM_CMD_GRAPH_START` timeout goes away. They do not
prove that its initiating cause is in the kernel rather than firmware or a
concurrent graph transition.

The pinned downstream source also predates upstream `2e926176` (a late
buffer-done NULL-dereference fix) and `9c9fb79` (zero-initializing a module
configuration structure). Do not silently add either to this A/B. The former
is a reasonable later safety candidate; the latter is tied to newer topology
Audio Interface fields and has not been shown to affect this baseline PCM
capture path. Each needs its own evidence and single-variable experiment.

Keep all userspace audio controls fixed for the kernel A/B:

- retain the current two-channel hardware declaration;
- retain the current DMIC0 + DMIC1 route and MultiMedia3 values;
- retain the same PipeWire/WirePlumber configuration and client request;
- do not combine this candidate with the IFPC, battery, UCM, fan, display, or
  suspend experiments.

Only after a kernel-only A/B should MM3 or DMIC routing be tested as a separate
single variable.

## Completed build and reproduction result

The candidate release is deliberately distinct from the running baseline:

- baseline:
  `7.1.0-100.20260730172655.pocketds.fc44.aarch64`;
- candidate:
  `7.1.0-101.20260829000000.pocketds.q6apmcs1.fc44.aarch64`.

`CONFIG_SND_SOC_QDSP6_APM=m`, so the distinct release gives the candidate its
own `/lib/modules/<release>` tree and vermagic. The RPM scriptlets were changed
to no-ops and cannot select `/boot/Image` or `/boot/boot/Image`.

The pinned source RPM was rebuilt twice from the same local inputs in two
separate Fedora 44 aarch64 roots. Both builds completed `Image modules dtbs`.
The result is reproducible at the runtime package-payload boundary:

- the compressed cpio payload is byte-identical, size `36983505`, SHA-256
  `85260d5738c60ade97496d6d3c2a5fd36260acc43594d4b2f9a9979e30ede3c4`;
- both extracted trees contain the same 499 entries, 341 regular files and 319
  modules, with manifest SHA-256
  `e669559d8d30b636b139825f790c966ece456693212543a0774230a6c2876da5`;
- `snd-q6apm.ko` is byte-identical and has candidate vermagic;
- the kernel-devel payload is also byte-identical.

The two RPM *container files* are not byte-identical, and this is not hidden by
the reproduction claim. Their main headers differ only in `BUILDTIME` (340
seconds); their signature headers consequently differ only in `SIGMD5`,
`SHA1HEADER`, and `SHA256HEADER`. The lead, header layouts, four padding bytes,
4128-byte signature reservation, zstd parameters and complete payload are
identical. Zeroing the four-byte build-time field produces the same raw main
header SHA-256, and `rpm -Kv` validates both header and payload digests for both
packages. `compare-rpm-containers.py` fails if any other byte or decoded tag is
different.

`compose-runtime.py` bound the candidate raw Image to its packaged Android
bootimg v0, replaced only the packaged DTB with the locked native-lid DTB, and
round-trip verified the result. The staged runtime image is SHA-256
`e85e3ee065ace1414dfe165f2b798964ee9f0cbf4cc66eb6e8d2303e766d6b8e`.
It remains an offline artifact, not permission to boot it.

The private evidence bundle is stored in the workspace sibling
`../q6apm-candidate-artifacts`. `manifest.json`,
`verify-reproduction.py`, `compare-rpm-containers.py`, and
`inventory-artifacts.py` bind and replay the source, package, container, and
host-artifact evidence. The bundle is private (`0700` directories, `0600`
files), fully materialized and checked for links, sparse/dataless placeholders,
mode, ownership, single-link files, complete readability and hashes.

## Development-only module swap decision

Do not hot-swap `snd-q6apm.ko`. The live module has active reverse holders and
stopping PipeWire does not prove that the complete ASoC card, GPR children, DSP
graphs and asynchronous callbacks can be torn down and restored fail-closed.
The persistent candidate also has intentionally different vermagic. The full
analysis is in `hot-module-ab-evaluation.md`.

## Reversible deployment design

Deployment must keep the baseline and candidate module trees and versioned boot
images side by side. The experimental package must not let its `%posttrans`
silently switch the ABL aliases; suppress that script for the experiment or
stage the package payload through a separately reviewed transaction. Adapt the
existing recovery transaction to the distinct candidate release instead of
overwriting the baseline's versioned image.

Before switching either ABL alias, verify the locked baseline rollback image
and the Mac fastboot RAM-boot recovery path described in
`tools/kernel-ab/rollback/` and `tools/kernel-ab/recovery/`. With the user
physically present, atomically point only the active ABL aliases at the locked
candidate image after the candidate module tree is installed. A failed boot is
recovered by RAM-booting the locked baseline image, which selects the untouched
baseline module tree, then atomically restoring both ABL aliases to the locked
baseline image. No partition flashing is required.

The current IFPC transaction lock assumes the baseline and candidate share one
kernel release and therefore names three identical aliases. It must not be used
unchanged for this module-bearing candidate.

The independent fastboot recovery gate is still open. Therefore candidate
installation, alias selection, normal boot, RAM boot, flashing, module copying,
audio recording and runtime testing are all **NO-GO**. The build evidence proves
artifact identity and runtime-payload reproducibility only; it does not prove
that the first `APM_CMD_GRAPH_START` timeout is fixed or that recovery works.
