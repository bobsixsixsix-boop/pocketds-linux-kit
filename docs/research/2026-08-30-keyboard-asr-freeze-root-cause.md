# Keyboard ASR freeze root cause — 2026-08-30

## Outcome

The three observed failure layers are separate:

1. WirePlumber retained an obsolete speaker-only profile after UCM gained a
   microphone. Re-selecting the KDE audio profile recreated the source and was
   a valid recovery action.
2. Pulse/PipeWire capture can time out in Qualcomm `APM_CMD_GRAPH_START` before
   any cloud request. Direct `plughw:0,2` capture avoids that failure in the
   isolated smoke test.
3. The whole-desktop freeze is the existing Adreno/GMU/display-fence problem.
   The final integrated attempt reproduced it without a Q6APM timeout or cloud
   request, so neither the API nor the audio timeout is a necessary cause.

## Final integrated timeline

- Keyboard process stayed active with zero service restarts.
- `voice: 录音文件安全检查失败` appeared at monotonic 452.645680.
- GMU `GPU_SET` timeouts began at 454.368073 and repeated six times.
- DPU `hangcheck recover` followed at 464.606024.
- Xwayland was then blocked in `dma_fence_default_wait`.
- The kernel had no Q6APM/ASoC capture timeout in this attempt and PipeWire had
  no corresponding error entry.
- The cloud recognition method was never reached.

The device was transactionally rolled back, rebooted at 05:39:44, and recovered
KDE, KWin, Xwayland, keyboard, speaker, and physical microphone without data
loss.

## Direct recorder compatibility bug

The direct recorder was producing an in-progress WAV, but the live readiness
probe rejected its header. alsa-utils `arecord` uses `0x7fffff00` as the data
length for an unbounded capture and writes RIFF length `0x7fffff24`; `end_wave()`
replaces those fields with observed lengths when recording stops. The previous
probe only accepted the live values emitted by libsndfile (`0`, `8`, or
`0xffffffff`).

Revision `239c033` accepts only the matching arecord placeholder pair while
retaining the strict final WAV validator. All 114 keyboard tests pass. Custom
voice remains disabled because correcting the WAV probe does not correct the
independent graphics hang.

## Decision at the initial incident

- Keep `CUSTOM_VOICE_SAFETY_ENABLED=False` until the independent GPU trigger
  has a reversible mitigation.
- Preserve ordinary keyboard input and ChatGPT voice.
- Keep the corrected direct-capture code as an implemented but not deployed
  user-facing path.
- Do not ask for another real voice attempt until the GPU/display path has a
  separately reversible candidate.
- Do not install a kernel, lock maximum GPU frequency, or run stress loops as
  part of this incident.

## Reversible resolution and attended smoke

Later bounded A/B work separated the GTK backend from the GPU runtime-power
transition. Native Wayland keyboard rendering still reproduced the GMU/display
failure with ASR and audio disabled, so it was reverted. Keeping only
`/sys/devices/platform/soc@0/3d6a000.gmu/power/control=on` prevented the failing
wake transition while leaving the existing `simple_ondemand` governor and
220 MHz to 1 GHz frequency range intact. Revision `449d2e6` installs this as
`pocketds-gmu-runtime.service`; disabling that service and rebooting restores
the kernel default. It is a mitigation for the observed runtime-PM trigger,
not proof that the underlying Adreno fault is fixed.

Three recorder/runtime bugs were then closed independently:

- `f812ff3` accepts both device-observed correlated arecord live-header pairs,
  including `0x80000024`/`0x80000000`.
- `426aabc` avoids a stale Flatpak AT-SPI proxy by bounding focus recovery to
  the active window and resolving the result back to a real pyatspi object.
- Device `arecord` returns 1 on both SIGINT and SIGTERM because its blocking
  ALSA read ends with EINTR; valid PCM remains under the live length fields.
  `6dfa9c9` records whether the orderly SIGINT was actually sent, accepts only
  that non-forced/no-failure return-1 case, validates the private single-link
  16-kHz mono S16_LE inode and matching arecord placeholders, then repairs only
  the two four-byte lengths after the process is fully reaped. Natural nonzero
  exits and every forced/failure path remain ineligible.

The final candidate passed 117 focused tests. A real direct-capture smoke
returned 1 as expected, finalized to a 16,684-byte WAV with RIFF 16,676 and data
16,640, and passed the strict upload reader before being deleted. Transaction
`voice-wav-finalize-6dfa9c9` applied and verified the seven keyboard artifacts.
The user then completed one real short Chinese phrase: the button stayed red
while recording, showed the explicit stop/recognition states, and inserted the
recognized text into the original field. Status is `SMOKE-PASSED`; ordinary
typing and ChatGPT voice were preserved, and further repetition is deferred to
real use.
