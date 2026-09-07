# PDS-011 local SenseVoice candidate

Date: 2026-08-28. The initial change was a source-only candidate selection: no
model, binary or Python package was downloaded or installed at that stage; no
microphone, cloud endpoint or live keyboard process was used.

## Why this candidate

The official [sherpa-onnx project](https://github.com/k2-fsa/sherpa-onnx)
supports offline inference on Linux aarch64 and publishes an
[aarch64 build path and prebuilt-binary location](https://k2-fsa.github.io/sherpa/onnx/install/aarch64-embedded-linux.html).
Its engine is Apache-2.0, but that engine license is not treated as a license for
arbitrary model weights.

The official [SenseVoice model documentation](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)
describes Mandarin, Cantonese, English, Japanese and Korean recognition, inverse
text normalization, and the exact `sherpa-onnx-offline` flags. Its int8 model is
about 226--228 MiB with a roughly 308 KiB token file. The published RK3588 CPU
measurements report RTF 0.049 on Cortex-A76 with three or four threads and 0.175
on Cortex-A55 with four threads. Those numbers justify a Pocket DS candidate;
they are not Pocket DS performance or accuracy evidence.

The upstream CLI writes progress/config to stderr and one recognition result as
JSON on stdout, as shown by its
[current source](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/csrc/sherpa-onnx-offline.cc)
and [official SenseVoice example output](https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/sense-voice/code/2024-07-17-itn.txt).
That gives this integration a much stronger parsing boundary than scraping a
human transcript line.

## Implemented boundary

The keyboard checks a fixed local binary, adjacent onnxruntime library, int8
model and token file before the existing WXASR and Whisper fallbacks. Each
artifact must be owned by the current
user, regular, single-link, non-group/world-writable and bounded; the binary must
be owner-executable. Invocation is a shell-free argv fixed to `zh`, ITN, CPU and
four threads. Stdout is read with a 45-second/256-KiB bound and stderr is
discarded. The parser accepts one strict UTF-8 JSON object with no duplicate or
non-finite values, `lang=<|zh|>`, `event=<|Speech|>` and a bounded normalized
text result. Whisper now uses the same bounded runner; unused WXASR output goes
to `/dev/null`.

Ten artifact/runtime fixtures and 29 keyboard integration tests cover the new
path. Since the three candidate files do not exist on the device, live behavior
is unchanged and no runtime service was restarted.

## Validation record

The source change is local commit
`129929948ca2d92af4237dfc72699995d8d6f5ce` and device commit
`f45fcdc525afee16d6853f6990bcf6cf4935f1a2`. Both resolve to the exact tree
`a0f427fa5c4fbae0f997957c8b07d04d73f5351d`; both worktrees were clean after
the change. Targeted 10-artifact/29-keyboard tests, lint and the complete
`make test` suite passed independently on macOS and the Pocket DS checkout.

The fixed binary, model and token paths were all absent on the device. A
read-only six-file UI plan still reported three `DRIFT`, one `MODE_DRIFT` and
two `MISSING` artifacts with activation deferred and no services restarted.
The concurrent PDS-008 soak kept invocation
`e6d19f0a75fe4c54ad46c8eb2d9a7faa`, PID `162791` and zero restarts across the
sync and device tests. This validates only the source boundary and fallback;
it is not a live ASR or UI deployment result.

## Remaining gates

The current device still has no physical PipeWire microphone source, so a model
benchmark cannot be honest yet. After audio input is restored, the same private
corpus must be run through SenseVoice, current WXASR and current Whisper and
evaluated with the existing CER/p95/RSS tool. Exact runtime/archive identities,
upstream version and model-specific terms are now recorded below, but release
still needs current-license revalidation and must not imply that a GitHub digest
is a detached artifact signature. An engine license alone does not close that
PDS-020 evidence gap.

## 2026-08-29 artifact lock and ARM smoke follow-up

The official sherpa-onnx v1.13.2 release commit remains
`13d0ae6c539d2809d32f5eaa3ef1db0c459d0b24`. The Linux aarch64 shared CPU
archive is locked at 26,674,802 bytes / SHA-256 `b5417842…330b`; the official
2024-07-17 int8 model archive is locked at 163,002,883 bytes / SHA-256
`7d1efa21…347e`. Both were downloaded only to a private Mac audit directory.
Their 46 and 12 tar members contain no links, devices, FIFOs, path traversal or
duplicate names. The runtime lock selects only four files: the 1.63 MiB CLI,
30.63 MiB `libonnxruntime.so`, 228.15 MiB model and 308.49 KiB tokens.

ELF inspection showed the CLI has `$ORIGIN` in its RPATH and directly needs only
the selected onnxruntime library plus Fedora system libraries. In isolated
Fedora 44/aarch64, placing those two engine files together worked without an
environment override. Five fresh invocations of the official 5.592-second
Chinese WAV all returned the exact ITN transcript `开放时间早上9点至下午5点。` in
0.407--0.445 seconds wall time. The observed child high-water RSS was about
703 MiB. These are VM compatibility and repeatability facts, not Pocket DS
latency, microphone accuracy or memory acceptance.

`sensevoice-artifacts-lock.json` now binds both archives, every installed file,
the sample, upstream revision and three notices/licenses. The engine Apache-2.0
text is byte-identical to the tagged source. The model license is byte-identical
to FunASR `MODEL_LICENSE` 1.1 at commit `05be4863…a29`; it requires attribution
and retaining the model name, and its revision clause means release engineering
must recheck current upstream terms rather than treating this snapshot as a
permanent legal conclusion.

The new prepare tool is offline and plan-only by default. It rejects archive or
member drift, unsafe tar types, unsafe paths, target drift, wrong host platform
and incomplete confirmation; execution stages only the four runtime files and
three notices, runs the official Chinese smoke, verifies final identities and
removes newly installed files if any step fails. Nine fixture paths cover plan,
execution/idempotence, confirmation/platform, tamper, symlink, drift, license,
strict JSON and smoke rollback. A real Fedora 44/aarch64 execution against both
locked archives installed exactly seven files in a disposable root, passed the
Chinese smoke, and its immediate second plan reported seven unchanged/zero to
install; the root was then deleted. The Pocket DS is deliberately unchanged
while the PDS-008 24-hour soak is active.
