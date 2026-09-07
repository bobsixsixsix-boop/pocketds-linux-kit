# Pocket DS keyboard ASR and Q6APM cold-start evidence — 2026-08-29

> 公开版更新（2026-09-08）：语音已改为用户自备的 OpenAI 兼容 API。下文涉及旧语音后端的提交、测试数量和实机结果均为历史记录，不能作为新 API 的验收。旧服务身份与账户细节不公开。

## Verified working layers

- The PipeWire default source is the physical internal microphone exposed by
  the SM8550 APS UCM profile at 48 kHz, signed 16-bit, two channels.
- The user verified ChatGPT's built-in voice input end to end. Do not change
  the ChatGPT launcher or microphone permission path to diagnose the keyboard.
- The historical private-backend credential provisioner passes its private-file checks. A bounded
  synthetic no-speech request reached the fixed HTTPS endpoint successfully;
  no credential is stored in this repository.
- One user-triggered keyboard recording through `parecord` produced a valid
  WAV, reached cloud recognition, and failed only at the later editable-target
  guard. That target bug was traced to the keyboard's own GTK accessible focus
  event and fixed by ignoring only events whose AT-SPI process id equals the
  keyboard process. Cross-application target revocation remains enabled.

## Reproduced kernel failure

Both the former ALSA PipeWire client and the PulseAudio-on-PipeWire client can
hit the same intermittent cold-start failure:

```text
qcom-apm ... CMD timeout for [1001002] opcode
... Failed to start APM port 110
... ASoC error (-110) ... VA_CODEC_DMA_TX_0
... MultiMedia3 Capture ... trigger FE cmd: 1 failed
```

`0x01001002` is `APM_CMD_GRAPH_START`; LPASS port 110 is
`VA_CODEC_DMA_TX_0`. The same dual-DMIC UCM route has also completed real
recordings, so this is not evidence that `CaptureChannels 2` or DMIC1 is an
always-invalid static configuration.

The keyboard now:

- resolves and pins the already-validated physical Pulse source instead of
  resolving `@DEFAULT_SOURCE@` again;
- records a private 16 kHz mono signed-PCM WAV through `parecord`;
- checks the recorder exit status and validates the private WAV before calling
  any ASR backend;
- reports `录音未成功写入` for a DSP/recorder failure instead of blaming
  Whisper or the cloud service.

The first bounded pre-ready retry candidate is NO-GO. Independent failure
injection found UI-blocking process cleanup, incomplete final-WAV TOCTOU
checks, callback ownership gaps, and a missing exact-target authorization gate
before cloud upload. The v4 replacement recorder ownership/state machine then
passed independent review with no P0/P1/P2 findings plus 106 keyboard, 37 ASR
and 9 voice-artifact tests. Device-local layout, routing and transaction tests
also passed. Commit `bce1901` was staged, applied and verified through six-file
transaction `keyboard-asr-retry-v4-20260829`; all live targets match and the
restarted keyboard service is active with zero crash restarts. No automatic
recording was run. Its six-second readiness window, one natural nonzero-exit
retry, cancellation and successful insertion still require a user-triggered
attended test. This userspace work is only a bounded mitigation; it does not
claim to repair the Q6APM timeout.

## Duplicate activation boundary

The only later keyboard-service evidence is a single cancellation at
`12:38:11` whose reason was `microphone connection cancelled by user`. That
reason was reachable only when `toggle_voice()` received another activation
while the session was still `CONNECTING`. A service log cannot prove whether
the source was a deliberate second touch or a duplicated GTK input event, so
the runtime must be safe under either case.

Candidate commits `c5d12f0` and `64acb62` make activation idempotent while the
session is `CONNECTING`, `STOPPING`, `RECOGNIZING`, or `PENDING_PASTE`. Only an
activation in `RECORDING` remains the user-owned stop action. Hide, editable
target change, shutdown, startup timeout, and genuine startup failure still
cancel their owning generation. Ignored activations produce one privacy-safe
log per generation/state rather than an unbounded stream.

The candidate passes 111 keyboard tests, including the real recorder-stop
owner/reap chain, cross-generation log deduplication, all terminal-state
restarts, and stale feedback-timer rejection. It also passes 37 ASR tests (one
platform skip), 9 voice-artifact tests, 15 provisioner tests, 17 transaction
tests, and the geometry, visibility, UI-design, lint, and diff gates. No audio
device was opened by these tests.

The device read-only plan found exactly one drift: keyboard main. Transaction
`keyboard-asr-idempotence-v2-20260829` staged six exact payloads and six live
preimages from clean revision `21e293c`, with manifest SHA-256
`8988bb37d3b33765d890f20ac75ba73f9d0d5d583425b78ed01aa8e441227b7d`.
Apply and verify reported all six targets `MATCH`. Keyboard main now has exact
source/live SHA-256
`631a07c50b6c879f847c84dfaf9bf5018470f9208ad1db0fcc33aca9cdedd971`.
The single explicit keyboard restart changed PID 527927 to 548490; the service
is active with zero crash restarts, and KWin PID 1001 plus GPU telemetry PID
126678 were unchanged. Deployment never opened an audio device. The remaining
gate is one user-attended activation, recording-stop, and insertion test.

This boundary is deliberately scoped to the custom keyboard. The user has
again verified ChatGPT built-in voice end to end. Its launcher, permissions,
and audio configuration remain a locked PASS and were not modified.

## Live WAV header compatibility

The pre-ready probe must accept the header that the installed recording stack
actually exposes while a file is still open; otherwise a security check can
turn every valid recording into a false failure.  The device has
`pulseaudio-utils-17.0-9.fc44` and `libsndfile-1.2.2-11.fc44`.  PulseAudio's
`pacat.c` opens the output with `sf_open_fd()` and writes frames, but does not
enable `SFC_SET_UPDATE_HEADER_AUTO`.

On the Pocket DS, a synthetic ctypes probe called `sf_open()`, wrote 320 zero
frames with `sf_writef_short()`, and then called `sf_close()`.  It used a
private `TemporaryDirectory`, did not open an audio device, did not create a
PipeWire stream, and removed the temporary file on exit.  The raw observations
were:

```text
open  44  RIFF=8    data_offset=36  data_size=0
write 684 RIFF=8    data_offset=36  data_size=0
close 684 RIFF=676  data_offset=36  data_size=640
```

This proves that `RIFF=8` and `data=0` are the live placeholders for the exact
installed stack.  The keyboard's live probe may accept those placeholders but
must still require actual bytes after the data header.  The final upload path
continues to require exact finalized RIFF and data lengths.

## Kernel candidate

The running source commit is
`a4975ce7c8eec079bd0cf7ec3890e100e016813f`. It lacks two upstream Q6APM
fixes that apply cleanly in order:

1. `3075ae5abbc370d2a9a01bd6d554a412d406f5bd` propagates module media-format
   errors instead of continuing with an incompletely prepared graph.
2. `daa7ffd765ae67a83e77dae32c66ce2d6d995d19` increments `start_count` only
   after a successful graph start and prevents stop-count underflow.

The reviewable patches and manifest live in
`experiments/kernel/q6apm-cold-start/`. They change only
`sound/soc/qcom/qdsp6/q6apm.c`.

A distinct-release candidate was built twice in independent Fedora 44 aarch64
roots. Its runtime and devel RPM payloads are byte-identical, its 319 modules
use the candidate release/vermagic, and an independent review found the offline
artifact set internally consistent. That result is **offline integrity GO**,
not runtime authorization. The two upstream fixes improve error propagation
and graph-start state accounting; neither proves that the first DSP timeout is
eliminated.

`CONFIG_SND_SOC_QDSP6_APM=m`, so a persistent candidate must use a distinct
kernel release and a distinct `/lib/modules/<release>` tree. It must not
overwrite the baseline module tree. The existing baseline rollback image has
SHA-256 `e4e0a38ab2f247dc67b6b4521f2fcc57123528ea50edda46ea08d305d9336c94`,
but independent fastboot RAM recovery is not yet validated. Therefore no
candidate kernel is authorized for installation or boot until the recovery
gate is completed.

## Next acceptance boundary

After independent fastboot RAM recovery is physically validated, run repeated
user-triggered cold recordings from a suspended source and require all of the
following:

- no Q6APM graph-start timeout or ASoC `-110`;
- valid private WAV creation on every attempt;
- transcript inserted only into the original editable target;
- speaker playback, suspend/resume, and subsequent microphone cold starts
  remain functional;
- ChatGPT built-in voice remains a locked PASS and is not modified as a
  keyboard-diagnosis target. Only an actually booted new kernel plus an
  explicit user request can reopen its regression check.

Until that matrix passes, keyboard ASR is improved and correctly diagnosed but
the intermittent kernel microphone failure remains open.
