# Third-party sources and license boundaries

This inventory covers the source tree reviewed at
`4609594451986c11dbce8e089c59c41e688eb2f6` and the source-publication preparation
that follows it. It is not a license grant for upstream material or a manifest
approving distribution of system images, downloaded applications, or model
weights. Preserve file-level notices and upstream license terms. A project-wide
license does not replace the licenses below.

## Material copied or adapted into this tree

| Repository material | Upstream and recorded revision | License and scope |
| --- | --- | --- |
| `components/audio/HiFi.conf`, the inline WSA884x two-speaker sequence identified in its comment | [ALSA ucm-conf SpeakerSeq.conf](https://github.com/alsa-project/alsa-ucm-conf/blob/00175aa645c482111d096c3d8230f182a875d286/ucm2/codecs/wsa884x/two-speakers/SpeakerSeq.conf); verified upstream reference `00175aa645c482111d096c3d8230f182a875d286` | BSD-3-Clause. Copyright (c) 2019, Advanced Linux Sound Architecture (ALSA) project. The sequence is adapted for Pocket DS; the upstream reference does not establish when it was originally copied. Full, unmodified [ALSA license](components/audio/licenses/ALSA-ucm-BSD-3-Clause.txt). This attribution applies to the copied sequence, not automatically to every audio helper. |
| Phosphor vector paths in `components/control-panel/plasmoid/contents/ui/main.qml` and `components/android-boot-switch/res/drawable/ic_launcher_foreground.xml` | Fixed comparison reference [Phosphor core `2b75f3ad12b420c9504ef05df8d2564a28f8500e`](https://github.com/phosphor-icons/core/tree/2b75f3ad12b420c9504ef05df8d2564a28f8500e); [Panel source/hash record](components/control-panel/plasmoid/PHOSPHOR-SOURCES.json), [Android record](components/android-boot-switch/licenses/Phosphor-SOURCES.json). Original copy revision remains unknown. | MIT; Copyright (c) 2023 Phosphor Icons. Complete, upstream-verified notice is retained in both [Panel](components/control-panel/plasmoid/licenses/Phosphor-Icons-MIT.txt) and [Android](components/android-boot-switch/licenses/Phosphor-Icons-MIT.txt) component trees. 24 Panel paths are identical; speaker-slash and the Android conversion are recorded as local adaptations and preserved. |
| `components/game-runtime/gamescope-control.xml` | Fresh import from [Valve gamescope `9f537fada5e51abf6df4da1c4caa24482ea7586b`](https://github.com/ValveSoftware/gamescope/blob/9f537fada5e51abf6df4da1c4caa24482ea7586b/protocol/gamescope-control.xml); [source/hash record](components/game-runtime/gamescope-control.sources.json) | MIT; Copyright © 2023 Valve Corporation. Complete notice remains embedded. Version 6 wire schema is unchanged; this import only adds upstream parameter documentation. Preserve the notice in redistributed source and generated protocol code. |
| `components/steam/launch-steam.sh`, `components/system/70-uinput.rules` | Reverified against [Azkali/Switchdeck `2b7f9e2fbe1e79a92789b62e29562751d4f717e5`](https://github.com/Azkali/Switchdeck/tree/2b7f9e2fbe1e79a92789b62e29562751d4f717e5), with SildurFX credit; [source, hash and adaptation record](components/steam/licenses/Switchdeck-SOURCES.json). This is a verification baseline, not an invented original copy revision. | GPL-3.0-only for these combined adaptations. Local headers now follow the verified GPLv3 grant, with no inferred later-version option. Full upstream [GPLv3 text](components/steam/licenses/Switchdeck-GPL-3.0.txt) and [NOTICE](components/steam/licenses/Switchdeck-NOTICE.txt) retained. Steam binaries remain separately proprietary. |
| Vendor userspace adaptations, including `packaging/pocketds-userspace/pocketds-fancontrol.pds1` and `components/fan/pocketds-fancontrol-connected-idle.py` | [Source lock](packaging/pocketds-userspace/source-lock.json), official COPR `pocketds-userspace-20260507-20260730174706.fc44.src.rpm`, SHA-256 `b0d509040195c19cc8b8ead5e296ffe26dddf34886f5a7c4428bf5adf921d561` | Upstream RPM spec and fan source declare GPL-2.0-or-later; retain individual source notices. The package-wide field does not replace separate BSD/MIT notices in material included by the package. The exact SRPM URL and spec/source hashes are retained in the lock. |

The project panel files and the separate `pocketds-mode-listener.py` already carry
GPL-2.0-or-later declarations. The mode listener's use of InputPlumber over D-Bus
does not make it part of the patched Rust daemon's source tree. Existing Moonlight
integration files carry GPL-3.0-or-later declarations. These are separate scopes;
directory names alone are not a basis for replacing their licenses.

## Patches against external source trees

The repository supplies deltas and integration/build instructions for the items
below, not their complete upstream implementations. When distributing a modified
binary, assemble the complete corresponding source and applicable upstream
notices for that exact build; a local patch file is only one part of it.

| Patch or integration | Pinned source | License evidence and boundary |
| --- | --- | --- |
| `components/inputplumber/inputplumber-d3932cb4-pocketds-haptics.patch` and the independent `inputplumber-d3932cb4-mouse-clear-buttons.patch` | InputPlumber 0.78.1, `d3932cb4e0de3eb47688e381b5eb5334ebac6254`; [deployed haptics source lock](components/inputplumber/inputplumber-haptics-source-lock.json), [mouse-clear source candidate lock](components/inputplumber/inputplumber-mouse-clear-source-lock.json) | [Upstream Cargo metadata](https://github.com/ShadowBlip/InputPlumber/blob/d3932cb4e0de3eb47688e381b5eb5334ebac6254/Cargo.toml) explicitly declares GPL-3.0-or-later. Preserve that scope for the patched daemon. The new candidate does not reuse the old binary's hash or deployment status. |
| `components/powerdevil/0001-pocketds-resume-lid-gate.patch` and its helper header | KDE PowerDevil `v6.7.3`; Fedora source/package provenance in [component README](components/powerdevil/README.md) | [Upstream dpms.cpp](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/actions/bundled/dpms.cpp) is GPL-2.0-or-later, with Dario Freddi (2010) and Kai Uwe Broulik (2015) notices. The local helper keeps GPL-2.0-or-later. |
| `tools/kernel-ab/patches/0001-arm64-dts-qcom-pocketds-native-lid.patch` and `0003-pocketds-unbound-cpuss-fan-trips.patch` | Pocket DS Linux device-tree source; verified reference `a4975ce7c8eec079bd0cf7ec3890e100e016813f` | The affected [Pocket DS DTS](https://gitlab.com/linux-pocketds/linux/-/blob/a4975ce7c8eec079bd0cf7ec3890e100e016813f/arch/arm64/boot/dts/qcom/qcs8550-ayaneo-pocketds.dts) and common DTSI declare BSD-3-Clause, with Teguh Sobirin/ROCKNIX notices. Do not blanket-label these device-tree changes GPL merely because they are built into a kernel. |
| `tools/kernel-ab/patches/0004-dpu-dsc-skip-incompatible-parity.patch` | Linux DPU resource manager; exact device build history in the kernel experiment records | The affected [dpu_rm.c](https://github.com/torvalds/linux/blob/v6.16/drivers/gpu/drm/msm/disp/dpu1/dpu_rm.c) declares GPL-2.0-only. Linux Foundation and Qualcomm Innovation Center notices apply to upstream source. |
| `tools/kernel-ab/patches/0005-panel-icna35xx-preserve-backlight-mode-flags.patch` | Pocket DS Linux panel source, verified reference `a4975ce7c8eec079bd0cf7ec3890e100e016813f` | [panel-chipone-icna35xx.c](https://gitlab.com/linux-pocketds/linux/-/blob/a4975ce7c8eec079bd0cf7ec3890e100e016813f/drivers/gpu/drm/panel/panel-chipone-icna35xx.c) declares GPL-2.0-only, Copyright 2025 Teguh Sobirin. Preserve this scope; a GPL-3.0-or-later project default cannot replace it. |
| Other kernel deltas in `tools/kernel-ab/patches/` and `experiments/kernel/` | Source commits, patch mail headers, and source URLs in the respective experiment manifests | Preserve each target file's license and existing authorship/Signed-off-by records. Kernel licensing is per file; the entries above do not certify all unlisted target files. No full kernel image is supplied by this inventory. |
| `components/emulation/android-watermelonds-pocketds/` | [WatermelonDS](https://github.com/SapphireRhodonite/WatermelonDS/tree/1e0a463d785448ab47f78a12e2a88241df288cf9), `1e0a463d785448ab47f78a12e2a88241df288cf9`; [build instructions](components/emulation/android-watermelonds-pocketds/README.md) | Upstream [LICENSE](https://github.com/SapphireRhodonite/WatermelonDS/blob/1e0a463d785448ab47f78a12e2a88241df288cf9/LICENSE) contains GPL version 3. This inventory does not infer an “or later” grant from the standard license text alone. Retain upstream/submodule licensing when assembling an APK. |

## Dependencies and optional downloaded artifacts

The following are used or fetched by installers/build tools; mentioning them does
not mean their binaries, data, or framework implementations are copied into the
source publication.

| Dependency | Version/source record | Distribution boundary |
| --- | --- | --- |
| Rime Ice dictionaries | [Lock](components/locale/rime-ice.lock.json), `fbb516b2786e4d5444383706d13c31c2e4d10c08`; [upstream license](https://github.com/iDvel/rime-ice/blob/fbb516b2786e4d5444383706d13c31c2e4d10c08/LICENSE) | Lock declares GPL-3.0-only. The tree contains project configuration and a download lock; preserve upstream license and notices if including downloaded dictionaries. |
| sherpa-onnx runtime | 1.13.2, `13d0ae6c539d2809d32f5eaa3ef1db0c459d0b24`; [artifact lock](components/keyboard/sensevoice-artifacts-lock.json) | Apache-2.0; retained [license](components/keyboard/licenses/sherpa-onnx-Apache-2.0.txt). The optional archive also supplies `libonnxruntime.so`; its own version, [MIT license](https://github.com/microsoft/onnxruntime/blob/main/LICENSE), and applicable third-party notices must be accounted for before redistributing that runtime bundle. |
| SenseVoiceSmall model weights | Same artifact lock; FunASR MODEL_LICENSE reference `05be4863fc94e3df37924a849b9790484ac74a29` | Separate [FunASR Model Open Source License Agreement 1.1](components/keyboard/licenses/FunASR-MODEL-LICENSE-1.1.txt), with [model attribution notice](components/keyboard/licenses/NOTICE.md). The model license is not the sherpa-onnx engine's Apache license or the SenseVoice code repository's MIT license. The lock does not mark this a public-release-ready model bundle. |
| Chromium and associated Arch Linux ARM runtime packages | Exact archive hashes and package versions in the Chromium runtime/provenance locks | Multi-component licensing; the current repository holds integration and provenance, not a grant to redistribute all dependencies. Include exact package licenses and third-party notices if later shipping the runtime. A verified package signature establishes source integrity, not redistribution terms. |
| Noto fonts | System package dependencies and font-family references; no tracked font binaries at the reviewed baseline | Noto CJK uses [SIL Open Font License 1.1](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE). Keep font-specific notices if later bundling font files. UI font selection is not a relicensing of application code. |
| Android framework, SDK/NDK, Qt/KDE, GTK/Fcitx, Wayland/XCB, PipeWire and other system packages | Installed or build-time dependencies | Their own package/source licenses apply. Android API use does not assign AOSP's license to an application, and AOSP licensing does not replace Linux driver licensing. Review the exact redistributed components if producing a system image or bundled application. |
| Steam and other separately installed applications | Launchers and integration settings in this tree | Steam binaries remain proprietary Valve material; the Switchdeck script license does not cover them. An application launcher is not a redistribution authorization for the application. |

## Fixed-reference verification on 2026-09-07

The Phosphor source records include immutable raw URLs, upstream SVG SHA-256,
upstream path SHA-256, local path SHA-256, and the exact upstream MIT-license
hash. Twenty-four Panel paths are byte-identical to the fixed reference. The
remaining `speaker-slash` path differs in its final arc; the Android `linux-logo`
path uses a local command/coordinate conversion and local fill/placement. These
two adaptations are retained unchanged and identified as adaptations, not exact
upstream copies. This comparison does not establish when the icons were first
copied. Both independently packaged components now contain the full MIT notice.

The gamescope XML is a new byte-identical import from the recorded Valve commit.
Its protocol interface, request/event order, argument types, enum values and
version are unchanged from the preceding local file; only one parameter's
upstream explanatory text differs. The source record pins the actual imported
bytes, while retaining the embedded license notice.

Both Switchdeck-derived files first appear in project commit
`f29ee2a6b676fa2220e97d6eef8abcb1e7ed5002` (2026-08-27), with a moving-branch
source comment. That historical record does not identify the original copied
upstream revision. The current candidate instead records an explicit comparison
against `2b7f9e2fbe1e79a92789b62e29562751d4f717e5`, retains the launcher adaptations,
and preserves the exact operative uinput rule from that reference's installer.
The launcher runtime statements and rule are unchanged by this source review.

The fixed [launcher](https://github.com/Azkali/Switchdeck/blob/2b7f9e2fbe1e79a92789b62e29562751d4f717e5/files/steam/launch-steam.sh)
and [installer](https://github.com/Azkali/Switchdeck/blob/2b7f9e2fbe1e79a92789b62e29562751d4f717e5/install-steam.sh)
credit SildurFX and say GPLv3; the retained upstream NOTICE identifies GPL-3.0.
Upstream commit [`46cfc317392023ee4bd84b09dee1d213d19d148b`](https://github.com/Azkali/Switchdeck/commit/46cfc317392023ee4bd84b09dee1d213d19d148b)
changed its earlier MIT notice to GPL-3.0. The current component headers therefore
use GPL-3.0-only, rather than retaining an unsupported upstream later-version
grant or applying the earlier MIT notice to newer code. This correction does not
replace the licenses of unrelated original project files.

## Items still requiring provenance work

- Keep the pinned source/hash records and complete notices when extracting a
  component into its own release; update the record when changing imported paths
  or protocol/script source. An unknown original copy date must not be silently
  turned into a claimed historical revision.
- Do not infer WatermelonDS's later-version option from a bare GPL text; its
  applicable source-file declarations require their own review.
- Artwork provenance/integrity records do not by themselves grant reuse rights.
  Only include wallpapers or other third-party artwork after its rights are
  established; this inventory grants no artwork permission.
- The reviewed baseline also tracked an ARM ELF at
  `components/game-runtime/pocketds-gamescope-observer`, alongside its C source
  and build script. A source-only export can omit that executable. If a release
  includes it, record the exact build source, toolchain and linked-library
  provenance and retain applicable notices.

This source inventory is intentionally separate from the stricter complete
system-image release gates described in `docs/release/README.md`.
