# Pocket DS state

> 公开版更新（2026-09-08）：语音已改为用户自备的 OpenAI 兼容 API。下文涉及旧语音后端的提交、测试数量和实机结果均为历史记录，不能作为新 API 的验收。旧服务身份与账户细节不公开。

- 2026-09-07 source-publication preparation is separate from the daily device.
  The daily input-handover fix `44985d1` is deployed and the user reports the
  left-button/lower-touch symptoms recovered; see
  `docs/research/2026-09-07-input-handover.md`. It is included in the source
  candidate. The separately discovered InputPlumber mouse-clear fix is a source
  candidate only, not a newly deployed daemon; see
  `components/inputplumber/MOUSE-CLEAR.md` and PDS-059. The entries below are
  dated historical observations, not a replacement for current runtime checks.
- 2026-09-06 03:19 JST audit repairs **deployed**, code `ffe2b2d`:
  Panel boot-result handling, brightness maintenance, stale gamepad event
  tracking, and all 39 DNF hardware/graphics/boot exclusion patterns are active.
  Two affected user services and Plasma switched to new verified generations;
  Panel applet and controller observer respond. Device repository includes the
  service-activation, file-metadata and daily-policy rollback installer repairs.
  Native runtime checks pass apart from the existing external Codex CLI warning.
  Brightness preferences and installed boot image unchanged; KWin, keyboard,
  telemetry, lid helper and InputPlumber PIDs preserved. Daily deep remains off,
  same RAM kernel/boot, counters3/0; no new physical lid or OS-switch acceptance.
  Backups and exact scope: `docs/research/2026-09-06-audit-deployment.md`.
- 2026-09-05 19:49 JST PDS-055 gamepad idle bridge **deployed**, code
  `3c66e33`: intentional input from the existing virtual Xbox controller now
  notifies KWin activity without remapping input, waking displays or changing
  the 300-second idle policy. Native paired IDLE → RESUMED → IDLE API test,
  54 component regressions and Fedora full `make test` PASS. New user service
  active/enabled, observer attached, zero restarts, about 18 MiB; runtime check
  has only the existing external Codex CLI warning. Screens remain dark when
  already off; installed boot image, lid helper, power policy, KWin output JSON
  and all existing desktop/input service PIDs unchanged, sleep counters3/0.
  Physical controller-only play >5 minutes and subsequent hands-off idle-off
  are **pending**, as is restart startup acceptance. Current gates intentionally
  do not cover a manually disabled single screen or a different input profile.
  No deep-sleep enablement or reboot. See
  `docs/research/2026-09-05-gamepad-idle-activity.md` for rollback and evidence.
- 2026-09-05 18:31–18:33 JST third v4 cycle: **179.442 s real deep,
  RTC IRQ166, counters3/0**, then physical opening 32 seconds after return.
  User confirms **开盖了 一切正常**; inspected screenshot shows upper desktop
  and lower Panel, both original modes/layouts and DPMS/backlights restored
  automatically, zero desktop restarts. This accepts RTC-return/later-reopen
  only; **Hall wake was not exercised**. No still-closed post-RTC backlight
  snapshot, so closed-lid darkness is not accepted either. No new observed
  DSC/storage error. RTC cleared, temporary policy removed, daily safely
  blocked; config/helper hashes unchanged, v4 RAM-only, installed v3 intact.
  Third one-use receipt is consumed; no fourth test prepared or dispatched.
  See `docs/research/2026-09-05-hall-wake-disabled-output.md` for exact evidence.
- 2026-09-05 18:00 JST paired ordinary close/open **passed user confirmation**
  (正常了). The existing helper handled a short Hall bounce, both original
  output layouts remained enabled, both DPMS/backlights returned on, and an
  inspected screenshot shows desktop/Panel. Counts stay2/0, RTC clear, no new
  observed DSC/storage errors; no operator display recovery or restart on this
  reopen. Open/closed KWin JSON snapshots are byte-identical (`0bcd7440…`).
  A third, separate one-use retained-layout Hall test passed native read-only
  open-lid preflight with config/helper/topology locks. Its additional closed
  physical-backlight checks run only before dispatch; **not dispatched**.
  This does not accept deep Hall resume or production
  daily ownership. Daily deep remains off, v4 RAM-only, installed v3 unchanged.
  See `docs/research/2026-09-05-hall-wake-disabled-output.md` for the remaining
  closed-lid RTC, native-policy and persistence acceptance boundaries.
- 2026-09-05 17:55 JST ordinary closed-lid checkpoint (no suspend requested):
  logind LidClosed=true; both outputs remain enabled in original layout but
  DPMS off and both bl_power=4. Sleep counters unchanged2/0, guard blocked,
  RTC clear. Paired closed config/DRM snapshots are saved. Opening comparison
  and user physical report remain pending. This is not another deep-sleep test.
- 2026-09-05 17:40 JST actual Hall wake on v4: **128.367 s deep, IRQ202**,
  counters 2/0. User reported upper dark despite working touch/lower display:
  **physical display acceptance FAILED**. Upper was connected/disabled, with
  no new DSC/storage error. KWin logged DRM permission failure and failed
  output configuration on thaw; repeated global DPMS could not enable upper.
  At 17:43 one scoped DSI-1 enable restored both outputs/desktop/Panel in an
  inspected screenshot, no compositor restart or reboot. This is recovery,
  not an automatic Hall-resume fix. Existing KWin closed-lid configuration now
  enables both outputs, unlike the 16:32 prior snapshot; exact change time is
  unresolved. No automatic-enable fallback or live JSON edit was deployed.
  Daily deep remains blocked, RTC clear, v4 RAM-only and installed v3 intact.
  Next check requires an ordinary physical close/hold/open without sleep,
  capturing paired display configuration before any further implementation.
  See `docs/research/2026-09-05-hall-wake-disabled-output.md`.
- 2026-09-05 user confirmation: **双屏和触摸都正常** after the first v4
  RTC-return / physical reopen test. This accepts that specific display/touch
  recovery, separately from screenshots. Hall-triggered wake during deep sleep
  is still pending. A second one-use wrapper is being prepared, locked to the
  same boot and predecessor receipt/counters 1/0, with a 180-second RTC rescue;
  preparation is not dispatch. Daily deep remains blocked; installed v3 intact.
- 2026-09-05 17:22 JST v4 physical lid reopening observed after RTC return:
  both displays automatically recovered without operator DPMS/output commands
  or reboot. Inspected screenshot shows the original desktop/Panel; 120/60 Hz
  and original layout returned. Lower owns PP0/LM0; returning upper now gets
  PP1/LM1/DSC1 with DSC0 free, confirming the candidate handles the previously
  rejected allocation case. No new DSC/storage errors. User visual/touch
  confirmation subsequently passed; this was **not lid-triggered wake from sleep**.
  Daily deep remains off and candidate RAM-only. See the v4 candidate report.
- 2026-09-05 17:19 JST first v4 closed-lid cycle returned after **119.153 s**
  of real deep sleep, woken by RTC IRQ166 (not lid wake). User confirmed closed
  lid before dispatch; post-resume LidClosed remains true, so opening/dual-screen
  touch acceptance is still pending. Counters 1/0; no observed storage/DSC
  errors, Wi-Fi returned and desktop services did not restart. RTC and transient
  sleep policy cleaned up; daily deep sleep remains blocked and installed v3
  unchanged. Upper is disabled in the closed-lid layout; lower owns PP0/LM0
  and its backlight is on after RTC resume, which is not yet accepted as correct.
  Do not run the one-use wrapper again or call this a completed lid fix. See
  `docs/research/2026-09-05-dsc-parity-candidate.md`.
- 2026-09-05 v4 DSC candidate RAM test: after explicit USB/reboot consent,
  v4 is running on boot `72a9286e-7eae-46cf-966a-6afc7d2102be`; installed v3
  `/boot/boot/Image` remains unchanged. The one-line allocator candidate fixes
  premature parity rejection in 6,580 of 262,144 exhaustive fixtures, with no
  baseline-success assignment changes. Both screens/layout and desktop/Panel
  screenshot, services and storage checks passed after RAM boot. Physical
  touch/lid acceptance is pending; **no v4 suspend cycle has run**, counters
  0/0. Daily deep sleep remains disabled. A boot-locked one-use attended lid
  wrapper passed read-only preflight; it must not be run until physical closure
  is coordinated. Normal reboot returns to v3, not this candidate. Details:
  `docs/research/2026-09-05-dsc-parity-candidate.md`.
- 2026-09-05 16:45 JST recovery after user-authorized normal reboot: new boot
  `2b2d4f79-4cc6-4557-a8ce-c1ebe600e6c1` restored both displays, the upper
  desktop and lower Panel in an inspected screenshot. Both backlights are on
  at exact targets 1515/2529; upper 120 Hz / lower 60 Hz, original layout and
  priorities 1/2 returned. Wi-Fi is connected, user/system failed units are
  empty, and no DSC allocation or storage error was observed on this new boot.
  Deep sleep remains disabled across reboot. This is recovery, **not a fix**
  for the physical-lid resume fault; no new sleep test was performed.
- 2026-09-05 16:36 JST superseding daily-sleep opt-in: physical lid reopen
  failed upper-screen acceptance. Kernel sleep counters reached 3/0, but DSI-1
  remained connected/disabled with bl_power=4; DSI-2 remained active. Explicit
  enable was rejected by the display driver with DSC resource allocation
  errors. Native deep sleep is now **disabled again** through the scoped
  rollback installer (KDE CanSuspend=false, root safely_blocked=true).
  The v3 image/kernel and user data were not changed. The upper display is
  still not recovered; no reboot or graphical-session restart was performed.
  See `docs/research/2026-09-05-lid-resume-display-failure.md`. Earlier successful
  RTC tests and kernel counters must not be read as physical-lid acceptance.
- 2026-09-05 explicit daily deep-sleep opt-in: the user requested activation
  after kernel installation. Native KDE now owns power-button/lid deep sleep,
  with AC/Battery display-off at 5 minutes and deep sleep at 10 minutes
  (LowBattery 2/3 minutes), without resume locking. A root pre/post gate locks
  the tested runtime/image/thermal tree and healthy storage; hibernation and
  inhibitor bypass remain disabled. The old lid daemon yields while deep is
  eligible. One real **native KDE** cycle slept 89.722 seconds with a test-only
  90-second RTC rescue, returned with counters 1/0, exact restored backlights,
  healthy desktop/services/network and an inspected screenshot; RTC was cleared.
  The earlier blocked native dispatch was a cached-capability/maintenance-lock
  issue, not a kernel sleep failure; deployment now detects that condition.
  Disable-to-baseline and kernel rollback inspection passed, then daily policy
  was re-enabled. Persistent install/import paths preserve this explicit opt-in.
  This supersedes the previous automatic-suspend-off statements below; no new
  reboot, physical lid/key acceptance or long-standby claim is made. See
  `docs/research/2026-09-05-daily-deep-standby.md` for exact limits and rollback.
- 2026-09-05 reversible v3 installation: the previously tested image now boots
  normally from internal `/boot/boot/Image`, SHA-256 `a136aeb0…`, on boot
  `17966808-9976-45e6-80d0-45b5dc920081`. Before final installation, an
  independent fastboot original-in-RAM / candidate-on-disk recovery succeeded,
  followed by baseline restoration and a normal original-kernel boot. Original
  image/modules and transaction receipts are retained. Final screenshots show
  the original desktop, lower Panel and the new **切换到 Android** desktop
  icon. Its confirmation window was inspected and dismissed without switching;
  it reuses the unchanged Panel backend and devinfo is unchanged. The launcher
  is included in future installs. Services/network, runtime/image identities,
  thermal-tree checks and disabled sleep guard passed. ARM64 `make test` passed;
  `make check` has only the existing external Codex CLI warning. The recovery
  archive is copied off-device and hash-verified. Automatic deep suspend
  remains **off**; installation does not close long-standby/UFS/GPU acceptance.
  This supersedes the earlier RAM-only status below. See
  `docs/research/2026-09-05-installed-kernel-trial.md` for exact recovery and scope.
- 2026-09-05 separately authorized five-minute follow-up: on the same v3
  candidate boot, exactly one `guard run 300` slept **299.809 seconds**, woke
  from RTC IRQ 166 and resumed without observed storage errors. Counters are
  3/0 (one device-debug plus two real cycles), root rw, and the original boot
  image is unchanged. Both backlights restored the exact previous brightness,
  screenshot showed desktop/Panel, services remained active with zero desktop
  restarts, and Wi-Fi reconnected in ~16 seconds. The user confirmed both
  screens, the lower Panel and touch were normal after this five-minute cycle,
  separately from the earlier one-minute confirmation. RTC/policy cleanup
  passed, automatic suspend remains disabled and
  no permanent kernel installation occurred. Private evidence was verified
  off-device; 42 pure guard/cycle/series fixtures passed on Fedora. This is a
  five-minute controlled pass, not a full charging/discharging/long-standby
  matrix or a measured standby-power result. See the extended
  `docs/research/2026-09-05-orphan-trip-candidate.md`.
- 2026-09-05 orphan-trip repair candidate: a board-local DT patch removes only
  28 unbound cpuss fan trips, preserving every surviving DT property, all 22
  critical trips, cooling bindings, TSENS IRQ wake and the exact v2 diagnostic
  kernel/modules. RAM boot and a devices-only smoke passed. One guarded real
  deep cycle then slept **59.424 seconds** and woke from **RTC IRQ 166**, not
  TSENS, without observed UFS/EXT4/block errors. Desktop/Panel screenshots and
  services recovered; the user subsequently confirmed both screens, the lower
  Panel and touch were normal. Wi-Fi recovery
  was slow after the device-debug test (~62 s, first address attempt failed)
  and ~16 s after real deep. This is a one-cycle candidate success, not daily
  standby acceptance or proof the historical UFS fault is fixed. The original
  on-disk 20260717 boot image remains unchanged, and automatic suspend remains
  blocked. See `docs/research/2026-09-05-orphan-trip-candidate.md` for the patch,
  verifier, exact image identity, evidence and remaining acceptance boundary.
- 2026-09-05 read-only early-wake follow-up: the exact candidate and live
  baseline retain 28 unbound `cpussN_fan*` passive trips across TSENS0 sensors
  1--4. Thermal-core window selection still includes them, and the TSENS
  upper/lower IRQ remains wake-capable during suspend. This is the leading
  normal-temperature-wake hypothesis, not proof of the exact historical
  sensor/edge. The uplow path also participates in Linux thermal protection;
  retaining only the separately named critical IRQ is not a sufficient safety
  guarantee. No fix or new sleep was executed. See
  `docs/research/2026-09-05-tsens-early-wake-investigation.md`.
- 2026-09-05 attended standby experiment: a temporary 7.1.12 diagnostic RAM
  boot passed dual-screen acceptance after enabling its required ST7703 lower
  panel driver. One devices-only PM test returned, then exactly one real deep
  suspend resumed without observed UFS/EXT4/I/O errors. The user confirmed
  both screens and touch after resume; Wi-Fi and desktop services recovered.
  It slept only 5.365 seconds: TSENS0 upper/lower threshold IRQ 23 woke it
  before the 60-second RTC deadline. This is EARLY-WAKE, not standby acceptance
  or proof the former UFS fault is fixed. Exact sensor/threshold is unresolved;
  thermal protection was not disabled. RTC cleanup and policy restoration
  passed; `AllowSuspend=no` is active. No boot-partition write occurred, and
  the original 20260717 image is unchanged. Final normal reboot back to that
  kernel passed, with healthy desktop/services and read-write root.
  See `docs/research/2026-09-05-deep-diagnostic-boot.md` for current artifact
  identities, rejected v1, recovery, private evidence hashes and next boundary.
- 2026-09-05 idle display-off lifecycle recovery: the apparent sleep/wake event
  was not a suspend. This boot has zero successful or failed suspend attempts,
  no `systemd-suspend` or kernel `PM: suspend` entry, and no accumulated suspend
  time. At 02:08:59 the KWin Wayland child instead aborted in Mesa/Freedreno
  while uploading a shadow/cross-fade texture during the first DPMS wake redraw.
  The KWin wrapper spawned a replacement compositor and the existing Plasma,
  keyboard and touchpad recovery paths restored the visible session, but
  PowerDevil terminated cleanly with its lost Wayland connection. Its vendor
  `Restart=on-failure` therefore left the 300-second idle action permanently
  inactive. Commit `ab14490` adds a two-second `Restart=always` user drop-in,
  installs it with the existing policy and makes the runtime audit report a
  dead PowerDevil explicitly. The complete ARM64 test suite passed. A controlled
  service-exit acceptance changed PID 58943 to 75947, kept the D-Bus owner and
  recorded one expected restart. With no further desktop input, at 02:24:11 the
  next 300-second interval put both backlights at `bl_power=4` and the lower LCD
  at `brightness=0/actual=0`, while the replacement PowerDevil remained active.
  Mac, device and the private Forgejo branch now resolve to the same clean
  revision. The underlying Mesa/Freedreno crash remains part of PDS-002; no
  graphics package, kernel, suspend policy or display effect was changed here.
- 2026-09-04 full dual-OS game-library cutover: the pinned v2 backup was
  converted into one shared uppercase `Roms` tree for Android Pegasus G and
  Linux ES-DE, then copied through a same-filesystem staging directory and
  promoted by directory rename. The live card now contains 22,670 ROMs across
  14 console categories, 21,189 shared media files and a metadata-only 71-game
  featured collection. All 43,890 managed files / 31,128,052,235 bytes passed
  on-card SHA-256 verification; the target inventory digest is
  `983c58672495983ff74ad0ac2ba3f193a33557f95e6034f8576aec575b22a9f0`.
  The pinned source manifest/inventory digests remain `7939c6f9…` and
  `0b22badf…`; 2,578 later Finder artefacts outside that inventory were not
  imported. Android metadata resolves every ROM and 21,189 media references,
  while all 32 NDS entries use the exact `9C33-6BBD` SAF URI and the Pocket DS
  WatermelonDS activity rather than DraStic. A real KDE ES-DE launch loaded all
  14 systems plus the collection in 5,124 ms with 22,644 visible games and no
  error/fatal/parse failure; ES-DE intentionally filters 26 arcade BIOS/device
  packages from its visible count. A Spectacle capture confirmed the frontend
  on the upper display and the working Panel on the lower display. Stopping the
  bounded smoke left no ES-DE/gamescope processes or failed user units. The old
  curated live tree and its verification log remain recoverable at
  `/mnt/pocketds-games/PocketDS/Backups/full-library-preimage-20260904-212549-7939c6f9`.
  SteamLibrary, Android standard directories, BIOS, saves and states were not
  changed. Android Pegasus category browsing and one launch remain the only
  owner-visible acceptance steps.
- 2026-09-04 controlled cold-boot closure: Steam was stopped cleanly and the
  complete game/input transaction
  `20260904-204320-271097595d248a09` deployed the repository generation with
  manifest `441c5e1f…`; the mode listener then completed its next cold start
  in `gamepad` with zero restarts and without restarting InputPlumber. The
  first reboot exposed a separate SM8550 ASoC race: `controlC0` existed while
  its deferred playback/capture backends were still binding, so WirePlumber
  permanently published a card with no profiles and the speaker enhancer
  retried against a nonexistent physical sink. Restarting WirePlumber after
  the card settled immediately restored `HiFi (Mic, Speaker)`, proving this
  was enumeration timing rather than lost UCM or firmware. Commit `b78918d`
  adds a bounded user-level pre-start barrier that replaces the image's
  unbounded device-node-only wait and requires the exact MultiMedia1 playback,
  MultiMedia3 capture and five mixer controls to remain present for three
  samples. The ARM64 full test suite passed before the two-file transaction
  `20260904-205653-b78918d` installed it. Controlled boot `b2544975…`
  then created the physical speaker, physical mic and enhanced sink on
  WirePlumber's first start; WirePlumber/filter-chain/enhancer were all active
  with zero restarts, the enhanced sink remained default, no profile,
  auto-null, XRUN or Q6APM timeout fault appeared, and both system/user failed
  unit sets were empty. The same untouched boot crossed the 300-second idle
  threshold with both panels at `bl_power=4` and the lower LCD raw brightness
  at 0. Post-boot `make check` passed with only the known external Codex CLI
  warning, and the device was again left with both displays off.
- 2026-09-04 repeated idle display-off rearm correction: the first acceptance
  stopped after one idle-off cycle and then used raw `kscreen-doctor --dpms on`
  for maintenance wake. The user correctly reported that the device remained
  lit afterwards. Live state still had the managed 300-second AC profile,
  zero active inhibitions and unchanged PowerDevil/Plasma processes, but a
  PowerDevil `wakeup()`-only retry also remained on past 345 seconds. A forced
  current-profile reload then turned the displays off again; a controlled
  `wakeup()` followed by `refreshStatus()` completed a second cycle at exactly
  300 seconds. Commit `99c2b68` makes the lid-open path perform that complete
  sequence while retaining exact dual-panel-on verification and its bounded
  hinge reconnect retry. The ARM64 22-case light-standby suite and complete
  `make test` passed. Transaction `20260904T1932-99c2b68` installed only the
  user helper and restarted only its service. Executing the deployed helper's
  real `LiveAdapter.wake_display()` restored both DSI outputs and raw lower
  brightness 2529, then a third untouched interval ended at exactly 300
  seconds with both `bl_power=4` and lower raw brightness 0. Final live
  `make check` kept all three relevant services active with unchanged
  PowerDevil/Plasma PIDs and only the pre-existing Codex CLI warning; the
  device was deliberately left with both displays off.
- 2026-09-04 idle display-off restoration: the managed Plasma 6 profile had
  carried `TurnOffDisplayWhenIdle=false` in AC, Battery and LowBattery since
  the original suspend-isolation commit, so later installs overwrote the KDE
  setting and an idle device could never blank. Commits `9cca40f` and
  `bd51378` separate safe DPMS from unsafe sleep: AC/Battery now turn both
  displays off after 300 seconds and LowBattery after 120 seconds, while
  dimming, lock-before-off, automatic suspend and resume locking remain off.
  The first live reload exposed a second issue: PowerDevil 6.7.3's
  `reparseConfiguration()` reloads only global settings, whereas its
  `refreshStatus()` also force-reloads the current AC/Battery profile and
  registers idle actions. The installer now uses the latter. The ARM64 device
  passed the complete non-hardware suite, then transaction
  `20260904T1836-9cca40f` atomically installed the profile without restarting
  PowerDevil or Plasma. A real untouched AC interval triggered at 300 seconds:
  both backlights changed `bl_power=0 -> 4` and the lower LCD changed raw
  brightness `2529 -> 0`. A bounded Wayland DPMS wake restored both to
  `bl_power=0`, the exact lower raw 2529, unlocked state and dual-display-ready
  within two seconds. PowerDevil stayed PID 1541 / zero restarts and Plasma PID
  97045; final live `make check` passed with only the pre-existing external
  Codex CLI warning.
- 2026-09-04 independent Git remote: the user-selected private Forgejo
  private development repository is now the independent off-device
  backup. The Mac worktree keeps the stale device-path `origin` untouched for
  audit history and uses the new `fnos` SSH remote for daily fetch/push. The
  complete current branch `design/panel-hinge-ui-20260901` (402 commits, no
  tags) was pushed at `40a90f033b482ecd856847f0194ac51268218526` and then
  cloned into a fresh directory over SSH; the clone passed `git fsck --full`,
  was clean, and resolved to the exact same commit. Authentication uses a
  dedicated, independently revocable Mac key; no private key, login token or
  password is stored in this repository. The Pocket DS must receive its own
  device key before its current bundle-backed remote is replaced.
- 2026-09-04 DPMS visual-wake correction: the first LCD-blackout acceptance
  incorrectly stopped at DPMS and sysfs values. The user's physical wake exposed
  a grey lower LCD with no content. A Spectacle capture after forcing both DSI
  outputs on was a byte-for-byte stable 26,739-byte all-black frame; KWin was
  active and unlocked, but `plasma-plasmashell.service` had exited cleanly at
  15:14 and remained inactive. Starting only that service restored the actual
  wallpaper, taskbar, desktop launchers and lower Panel in a 5.23 MB screenshot.
  Commit `22efdc1` now gives clean shell exits a one-second systemd respawn and
  makes the Switchdeck watcher restore any Plasma instance it stopped even when
  launcher cleanup interrupts the watcher. Live clean-quit acceptance changed
  PID 95652 to 97045 in 1.893 seconds with `NRestarts=1`; the following DPMS
  cycle reached bottom `bl_power=4/raw=0/actual=0`, woke to exact raw 2529 in
  1.073 seconds, and produced a visually reviewed, non-black 5,257,066-byte
  dual-display screenshot. After the Panel's normal low-frequency refresh, a
  second 5,254,980-byte screenshot showed its lower slider back at 62%. Future
  display-off acceptance requires DPMS, backlight, Plasma ownership and an
  actual non-black post-wake screenshot together. Rollback is
  `20260904-1710-plasma-lifecycle`; no KWin, Steam or system reboot was used.
  The user then completed the physical off/wake check and confirmed that it
  looked correct, closing both the true-blackout and visual-wake smoke gates.
- 2026-09-04 true LCD blackout correction: a controlled dual-DPMS cycle proved
  KWin correctly reports both outputs off and changes both backlight devices to
  `bl_power=4`, but the downstream SY7758 driver leaves the lower LCD's raw and
  reported brightness at 2529, matching the user's visible backlight glow.
  Writing that exact backlight to zero while DPMS is already off produced
  `brightness=0` and `actual_brightness=0`; DPMS on plus the original raw value
  restored 2529 exactly. The brightness daemon now owns this bounded workaround
  for both power-button and lid paths: blank only bottom after kernel powerdown,
  hold zero while off, and immediately replay the persisted preference on wake.
  The privileged helper rejects the action unless its exact bottom backlight is
  already marked off, and normal Panel brightness remains restricted to 5–100%.
  The first daemon acceptance exposed a 4.016-second wake restore caused by the
  former five-second off-state cadence; a one-second full-loop retry still took
  1.977 seconds end to end and was also rejected. The blanked state now has a
  250 ms fast path that reads only bottom `bl_power`; full state parsing and the
  privileged helper remain confined to the two transitions.
  Final daemon-controlled acceptance on `c5f62c8` passed: off completed with
  `bl_power=4`, `brightness=0`, `actual_brightness=0`; a steady-off wake restored
  the exact raw 2529 preference in 1.126 seconds and both DSI outputs returned
  on. Deployment rollback is the `20260904-165743-brightness` backup pair.
- 2026-09-04 unattended post-audit repair: commits `e1fd8d3` and `4253496`
  correct five source/runtime drifts without rebooting, switching OS, launching
  a game or exercising haptics. With the lid already closed, KWin legitimately
  removes physical `DSI-1` from its DPMS status; light-standby now accepts that
  only on the off path while still requiring `DSI-2=off`, and continues to
  require both panels on after open. The former five-second DPMS/AT-SPI loop
  stopped immediately: the service is active with zero restarts and two-minute
  post-install counts are zero for both failed DPMS verification and keyboard
  AT-SPI noise. The audio controller now parses `pactl` under the C locale and
  reports the active enhanced sink correctly from a zh_CN session. ES-DE's
  read-only check and log summarizer now follow only the exact managed
  `/mnt/pocketds-games` root; live results are 14 systems, 215 regular ROM-tree
  files and zero warning/error/fatal log records. System console/X11 and the
  user manager are all `us`, matching KDE instead of the stale `am` base.
  UPower's `Auto` action had resolved to `Ignore` because suspend is disabled;
  a dedicated conf.d drop-in now keeps the 2% threshold and explicitly selects
  clean `PowerOff`, verified live at 100% battery with PowerDevil still active.
  The sole stale Steam failed-unit record was reset after exact-name matching,
  and the complete non-hardware suite plus live `make check` pass. The check
  contract now tests the lid boolean independently from Wayland and dual-panel
  readiness, so an SSH check while closed no longer contradicts its own
  successful `SW_LID` result. Rollback
  material is under the user audit-fixes transactions for `e1fd8d3`,
  `e1fd8d3-keymap` and `4253496-upower`. The mode-listener cold-start wait is
  implemented and fully tested, but its whole-stack installer correctly
  deferred live deployment while Steam was running; no safety guard was
  bypassed. Mac and device worktrees are clean and aligned. At the time of that
  audit there was no independent Git remote and the Mac `origin` still named
  the deleted old device path; the later Forgejo setup above closes the remote
  backup gap without rewriting that historical remote. Codex CLI and
  `~/.codex` login state are still absent on the rebuilt system, so the quota
  cache correctly reports unavailable and still requires a separate user
  account action.
- 2026-09-04 dual-display lid action correction: the restored lid sensor was
  physically exercised and changed UPower state correctly, but closing the lid
  left the lower display lit. The already smoke-passed short power-button action
  still turned both displays off, and a controlled `kscreen-doctor --dpms
  off/on` test independently returned both exact outputs (`DSI-1` and `DSI-2`)
  to the requested state. The light-standby service therefore again owns one
  verified global KWin DPMS transition for lid close/open; PowerDevil lid
  actions are disabled in all three profiles while its power-button action
  remains 64. Transaction
  `/var/lib/pocketds-linux-kit/lid-dpms-transactions/20260904T115146` deployed
  the bounded change. Live check reports `display_owner=pocketds-light-standby`,
  both DSI outputs `on`, a valid Wayland environment, no state error, and
  service `active/running` with zero restarts. One new physical close/open is
  still required before this regression returns to SMOKE-PASSED.
- 2026-09-04 Steam desktop/gamepad round-trip correction: the 11:59 warm-session
  menu round trip passed, but the first reboot test exposed a second entry path.
  At 12:14 the ordinary desktop icon launched managed Gamescope with
  `STEAMDECK_MODE=false`; Steam's saved startup preference entered `uimode=4`
  without a `-gamepadui` argument, so the Deck-looking menu never invoked the
  project selector. Managed Gamescope cold starts now always force a genuine
  Deck environment, while only the independent KDE desktop-return service may
  own a false-mode Steam. Both the application-menu entry and the separate
  physical Desktop copy explicitly request true mode, and the bootstrap template
  plus installer validation enforce the same invariant. Transaction
  `/var/lib/pocketds-linux-kit/steam-session-transactions/20260904T1216-bigpicture-coldstart`
  deployed the correction; the rebuilt live client has `STEAMDECK_MODE=true`,
  `-steampal -gamepadui -steamdeck`, Gamescope `-e`, and `uimode=4`. At 12:30:32
  the user selected the real menu action: the gamepad unit and Gamescope exited,
  KDE/Plasma/Panel remained active, the desktop unit became active, and its
  steamwebhelper reached `uimode=7`; no Pocket DS Steam unit was failed.
- 2026-09-04 lid display-off restoration: after the Android/Fedora rebuild the
  running kernel is `7.1.0-100.20260717114522.pocketds.fc44.aarch64`, whose
  live DTB does not expose the former GPIO 166 `EV_SW/SW_LID`; UPower therefore
  reported `LidIsPresent=false` even though all three PowerDevil profiles still
  had display-only `LidAction=64`. The original active-low BU52053NVX path is
  restored without touching the boot partition: official Fedora
  `libgpiod-utils-2.2.5-1.fc44.aarch64` plus a conditional root bridge monitors
  `gpiochip4` line 166 and publishes `Pocket DS Lid Switch` through uinput only
  when no native SW_LID exists. Transaction
  `/var/lib/pocketds-linux-kit/lid-switch-transactions/20260904T112716` installed
  the hardened service. Live evidence after start is `B: SW=1`,
  `ID_INPUT_SWITCH=1`, `LidIsPresent=true`, `LidIsClosed=false`, service active,
  and zero restarts. The source/installer now keep this adaptive
  fallback across reinstalls and automatically stand down when a native-lid
  DTB returns. No kernel, DTB, boot image or reboot was changed. Its physical
  close/open exposed the separate dual-display action regression recorded
  above rather than a remaining sensor failure.
- 2026-09-04 post-reinstall application/runtime recovery: desktop files had
  been restored before their external runtimes were proven usable. The pinned
  Chromium 151.0.7922.137 ARM64 V4L2 runtime is now rebuilt from the five
  retained signed Arch Linux ARM archives; signatures, package metadata, all
  54 compatibility entries, the provenance receipt and the live runtime lock
  pass, and a real Wayland Chromium process is running from the locked `/opt`
  root. Wiliwili was installed on Flatpak `stable` while the supervisor still
  requested `master`; commit `e44a38c` fixes the branch and a real launch
  reached a unique numeric Flatpak instance before cleanly restoring input.
  ES-DE's card check incorrectly rejected systemd automount's two `findmnt`
  rows; `eb4ec00` verifies the live mount after content access instead. A real
  ES-DE launch used the TF-card `--home`, Gamescope reported 111.6 FPS, and the
  Panel selected `gamescope-presented`; stop removed the session record and
  restored `gamepad`. Steam game shortcuts also bypassed this managed runtime,
  and Switchdeck's background watcher survived the client, leaving a second
  virtual Xbox controller. Commit `8863e1b` wraps Stardew/Brotato through the
  managed desktop Steam path and reaps the watcher. A real desktop-mode Steam
  launch contained no gamepad-UI flags and exposed only the InputPlumber Xbox
  Elite target; after stop, Steam, its watcher and the session record were all
  absent. The haptics source is byte-equivalent to the newly merged upstream
  0.79 implementation; a bounded 30%/120 ms Rumble+Stop completed with the
  same InputPlumber PID and zero restarts. Forty-one stale failure records from
  the old Wiliwili/ES-DE/Chromium launches were reset after their exact names
  were checked; no failed user units remain.
- 2026-09-04 post-reinstall daily desktop completion: the clean Fedora install
  had retained the keyboard feedback setting but omitted all four parts of the
  pinned InputPlumber haptics sidecar, so both keyboard Pulse and ordinary game
  rumble were unavailable. Transaction `20260903T170807Z` restored the exact
  AArch64 `d3932cb4` / fixed-50 ms candidate, SHA-256
  `2c06a4cbfaa2aa93c923b1dc790bbaf15ae44e048662840cce0705bf2d7df244`
  and Build ID `b2034bf875122a8810bb543db0faf583f3942331`. The service now
  runs `/usr/local/libexec/pocketds-inputplumber-haptics`; Pulse, Rumble and
  Stop are present on D-Bus, one bounded 70% / 50 ms Pulse completed, the
  original `gamepad` mode was restored, and InputPlumber remains active with
  zero restarts. The generic installer now checks the exact binary hash plus
  device profile, service drop-in and policy, and reports the installation as
  incomplete instead of silently accepting Fedora's non-haptic fallback.
  Current-session physical confirmation of the restored pulse remains the only
  attended step.
- 2026-09-04 Rime Ice and desktop access completion: the already pinned full
  Rime Ice tree remains at upstream commit
  `fbb516b2786e4d5444383706d13c31c2e4d10c08`; its main dictionary imports
  8105, base, extension, Tencent and supplemental dictionaries. The daily-use
  override explicitly enables the personal user dictionary, sentence
  composition and both completion paths without enabling the optional 41448
  rare-character dictionary that would pollute normal candidates. Fcitx was
  cleanly restarted through its KDE autostart unit, rebuilt
  `rime_ice.schema.yaml` and the 60,653,172-byte table, created
  `rime_ice.userdb`, and returned with Rime active and zero restarts. The
  reversible user transaction is `rime-daily-20260904.UCINLW`. Nine 0755,
  regular, validated desktop launchers now cover every requested application
  actually installed on this system: Steam, Moonlight, Wiliwili, ES-DE,
  Stardew Valley, Brotato, Chromium, melonDS and a clearly labelled Tailscale
  status launcher. Chromium's external runtime and the managed Steam game
  targets are now additionally required before their shortcuts are created.
  Tailscale remains installed but pending account login; the
  shortcut does not pretend otherwise. Desktop transaction
  `desktop-shortcuts-20260903T171821Z.xcMc1u` preserved the previous Steam
  link. Commit `5e59e2c` adds repeatable installers and policy tests; the full
  device `make test` suite passes.
- 2026-09-04 performance-authority reboot fix: the previous recovery left both
  `tuned.service` and `power-profiles-daemon.service` enabled even though their
  units conflict. On the first later KDE login, graphical.target started PPD,
  stopped TuneD, and every Panel performance request failed because
  `com.redhat.tuned` was no longer activatable. The required PPD package stays
  installed for `pocketds-base`, but its unit is now disabled and masked so
  neither graphical.target nor system D-Bus can race TuneD. The installer
  enforces and verifies that single-authority state before accepting the
  active profile. Live repair and the reversible three-profile matrix are
  recorded under the PDS-043 transaction; no kernel, boot, display or package
  changes are part of this fix.
- 2026-09-03 Simplified Chinese and Rime Ice are restored as a bounded,
  reproducible user-space install. DNF transaction 9 added 22 packages and did
  not upgrade or remove anything: the active ARM64 path is Fcitx5 5.1.21,
  `fcitx5-rime` 5.1.14, librime/librime-lua 1.16.1, the zh_CN glibc locale and
  Noto CJK Sans/Mono fonts. Weak dependencies are disabled, so the competing
  IBus stack remains absent. `fcitx5-qt6` is deliberately absent because the
  current Fedora package requires a different Qt private ABI; native Plasma
  Wayland instead selects Fcitx5 through KWin while XWayland retains XIM.
  System and Plasma locale are now `zh_CN.UTF-8`/`zh_CN`; a new login is still
  required for all already-running Plasma processes to inherit them. The clean
  image had also filtered 560 `zh_CN/LC_MESSAGES` paths from 245 already
  installed RPMs. Those files are now restored from the exact matching Fedora
  Koji builds after all 245 RPM signatures and identities were verified: 555
  regular catalogs and five package-defined, locale-local symlinks were staged
  before the first system write, installed without overwriting any path, then
  hash/readlink-verified. A full rpmdb rescan reports zero missing zh_CN message
  catalogs. Rime Ice came from official commit
  `fbb516b2786e4d5444383706d13c31c2e4d10c08`,
  passed Git object verification and completed its first `rime_ice` build.
  After the one-time dictionary compile, a clean Fcitx restart reports `rime`,
  51 MiB RSS, active/running, exit status 0 and zero restarts. The protected
  Qt/Plasma/Mesa/kernel/firmware package inventory is byte-identical before and
  after installation. The verified receipt and preimages are under
  `/home/pocketds/.local/state/pocketds-linux-kit/locale-transactions/zh-cn-rime-20260903T140355Z`;
  the matching signed-catalog receipt is
  `/home/pocketds/.local/state/pocketds-linux-kit/locale-transactions/zh-cn-catalogs-20260903T142102Z/receipt.json`.
  Owner-visible Chinese typing after the new login remains the final attended
  check.
- 2026-09-03 shared game-card recovery: Fedora now runs ES-DE 3.4.1 ARM64
  (`b84eababe6d6388223cf8b1658bb237bc0125362acaae70e1ece55397c1eb414`)
  directly against the one exFAT mount at `/mnt/pocketds-games`. Commits
  `7c171c1`, `a1f313e`, `e38b00d`, `4947527` and `37bb411` add the dedicated
  system map, keep ES-DE gamelists/media/collections on the card through the
  official `--home` path, make the generated system definition safely
  upgradable, accept the four uppercase PS1 `.PBP` entries and remove the
  invalid MAME scraper platform. The final device transaction is
  `20260903-184412-377c7d32b037b74d`; all five focused ARM64 test groups pass,
  InputPlumber was not restarted, and a real launch shows the console carousel
  above the still-working lower Panel with zero ES-DE warning/error/fatal log
  entries. One physical game launch/play/exit remains the attended acceptance
  step; Android Pegasus console-category browsing also remains owner-visible.
- 2026-09-03 Panel control recovery: the clean internal install omitted the
  per-user brightness writer/service and did not include TuneD, while the
  retained performance profiles still requested an unsupported 3.36 GHz big
  core ceiling and an inoperative global boost switch. `tuned-2.27.0-1.fc44`
  is now installed and active with the profiles aligned to the current
  kernel-reported 2.9568 GHz ceiling; `tuned-adm verify` and the reversible
  powersave → balanced → performance → balanced matrix pass. The required
  `power-profiles-daemon` package remains installed; its unit and TuneD declare
  each other as conflicts, so TuneD is the active authority without removing
  `pocketds-base`. The brightness helper/service is installed, active with zero
  restarts, and both displays passed 37% → 38% → 37% hardware writes. Fan,
  volume, input mode, lid timeout and game-limit writes also passed and were
  restored. Panel status now reports `brightness_write_status=ok`; its QML
  separates readable telemetry from write capability and source/live hash is
  `bf0f3c51eef960fa095cd65c414e3c26cde2aaf900f2a2b8746a2a7614bdb288`.
  Fedora `plasma-milou-6.7.4-1.fc44` was added without upgrading or removing
  the protected graphics stack, restoring the missing `org.kde.milou` module.
  DNF history entries are 6 (TuneD and 13 dependencies) and 7 (Milou only).
  Rollback points are
  `/var/lib/pocketds-linux-kit/backups/20260903-110258-power-profile-kernel-sync`,
  `/var/lib/pocketds-linux-kit/backups/20260903-110325-brightness`, and
  `/home/pocketds/.local/state/pocketds-linux-kit/backups/20260903-110358-panel-control-capability`.
  The final read-only check has no control/service/hardware warning; only the
  separately distributed Chromium V4L2 runtime and Codex CLI remain absent on
  this clean install; ES-DE has since been restored as recorded above.
- 2026-09-03 superseding recovery state: official AYANEO Android is restored
  and the audited 50/50 internal Android/Fedora layout has completed its first
  internal Fedora boot with the rescue TF removed. `/dev/sda13` is `/`,
  `/dev/sda12` is `/boot`, Wi-Fi and both displays are healthy, and the core
  Panel/keyboard/touchpad/telemetry services are active. The OS switch
  implementation preserves the ABL boot
  source and changes only the verified 4 KiB `devinfo` `BootMode` byte after a
  full normalized-image check, backup and readback. Linux exposes this as a
  confirmation-gated secondary Panel action. Android now exposes the signed
  native launcher app `切换到 Linux` (`li.azka.pocketds.dualboot`, version 2.0):
  its real-device read-only probe passed against the current internal
  `ROCKNIX` `/boot` and `STORAGE` Fedora root, and the app is installed in the
  AYANEO launcher. The earlier root script remains only as a rescue fallback;
  the current app does not depend on a TF card. The destructive Android →
  Linux leg has not yet been pressed for this v2 build, so the owner-observed
  round trip remains pending. Older Linux-only statements below are retained
  only as chronology and are not current device truth.
- AYANEO controller rumble and keyboard haptics are deployed as a bounded
  daily-use candidate. InputPlumber 0.78.1 at upstream PR #670 commit
  `d3932cb4` drives the Pocket DS `4001:0428` motors while the Fedora 0.75.2
  package remains untouched as rollback. Games use ordinary virtual Xbox
  force feedback. After the user confirmed the former 25% maximum physically
  moved the motors but was extremely weak, attended calibration established 50
  ms as the practical motor start threshold and confirmed true 30%, 50% and 70%
  pulses were all clearly perceptible with useful strength separation. The
  formal keyboard mapping is now `DEPLOYED` and `SMOKE-PASSED`: 30%, 40%, 50%,
  60% and 70% all use 50 ms. The live backend caps only keyboard Pulse at 0.70 /
  50 ms; generic game Rumble remains unchanged. Its live AArch64 SHA-256 is
  `2c06a4cbfaa2aa93c923b1dc790bbaf15ae44e048662840cce0705bf2d7df244`.
  Build ID is `b2034bf875122a8810bb543db0faf583f3942331`. Formal D-Bus Pulse
  calls at 30%, 50% and 70% / 50 ms each succeeded once and the supervised flow
  automatically restored `gamepad`. InputPlumber PID 522514 and keyboard PID
  523144 are active/running with `NRestarts=0`. The worker still runs at most
  once per 55 ms and only in stable `joymouse`; mode changes remove the game
  target before a Stop queue barrier. This proves the keyboard haptic path, not
  vibration inside a game; one known rumble-capable title remains an attended
  check and many DS/PSP titles do not emit rumble. Current rollback points are
  sidecar transaction `20260831Tfixed50`, whose preimage is the
  `050500a1…f97a9` 25% / 40 ms candidate, and keyboard transaction
  `feedback-fixed50-20260831`, which preserves the exact previous keyboard
  files. Sidecar transaction `20260830T192143Z` is already rolled back and is
  not current. Fedora's `/usr/bin/inputplumber` and packaged configuration
  remain untouched.
- Keyboard feedback controls are deployed inside the keyboard: the former
  browser action is now Settings, sound and haptics each have an independent
  switch, five levels and a preview action, and the private 0600 configuration
  is replaced atomically. Sound volume no longer uses libcanberra's permanent
  sample cache. The original -60..-36 dB range became practically inaudible
  once per-play volume worked, so the deployed range is now -30, -24, -18,
  -12 and -6 dB. A live level-4 loopback measured peak 5,890 from the original
  sample peak 23,448, exactly the expected -12 dB / 25.1% amplitude. The
  original Calculator action hid the keyboard and used a short-lived
  exact-match KWin rule to place KCalc on the upper DSI-1 display; a real
  launch screenshot confirmed KCalc above and the normal Panel below.
  At the user's request, the 2026-09-06 source update replaces this action
  with Terminal (`org.kde.konsole`). It still hides the keyboard before launch;
  the temporary rule places only the first newly added Konsole window on
  DSI-1 and leaves existing terminal windows where they are. A subsequent
  `passwd` failure exposed the direct launcher's inheritance of the keyboard's
  private user namespace, which maps only UID 1000. The source now delegates
  `/usr/bin/konsole --separate` to a transient user service, preserving keyboard
  isolation and avoiding reuse of an affected terminal process. `934c908` is
  deployed: an actual keyboard-button launch placed a new Konsole on DSI-1,
  with its shell sharing the user manager's initial namespace and full UID
  mapping; the keyboard remains isolated. Existing-password authentication
  reached the new-password prompt in a separate managed PTY and was cancelled
  without changing the password. Local checks pass 191 keyboard and 20 UI
  tests; device acceptance is `SMOKE-PASSED`. Existing affected terminals must
  be reopened. See
  [terminal launch isolation](research/2026-09-06-terminal-launch-isolation.md).
  The earlier
  feedback verification passed 174 keyboard tests, 17 UI contracts,
  the input-mode mock and both sidecar suites. Latest rollback snapshots are
  `keyboard-layout-transactions/20260831-feedback-runtime-final`,
  `keyboard-layout-transactions/20260831-feedback-preview-style` and
  `inputplumber-haptics-transactions/20260831-reviewfix-43eb41fb`.
- The curated ES-DE library and Nintendo DS daily-use path are now
  `SMOKE-PASSED`. The user launched 《新超级马里奥兄弟》 from ES-DE, confirmed
  correct dual-screen placement and playable controller input, then used the
  two-stage Menu+View confirmation to close only standalone melonDS and return
  to ES-DE. Commits `a424f39`, `e64d0cc` and `c52de99` implement that path;
  transaction `20260830-160045-1879aad407ed731f` deployed it without restarting
  InputPlumber. The first failed attempt remains useful evidence: all four
  Menu/View press/release events arrived, while `ProtectHome=true` hid the user
  runtime journal until `c52de99` removed that dependency.
- Custom keyboard ASR is now `SMOKE-PASSED`. The persistent
  `pocketds-gmu-runtime.service` keeps only GMU runtime power management awake;
  the GPU remains on `simple_ondemand` and returns to 220 MHz instead of being
  locked at 1 GHz. Direct `plughw:0,2` capture avoids the observed PipeWire
  Q6APM startup fault. The runtime accepts the two device-observed correlated
  live arecord header pairs, recovers the focused editable through the active
  AT-SPI window when a stale Flatpak proxy blocks generic traversal, and repairs
  the exact SIGINT/EINTR live-WAV shape after the recorder is fully reaped.
  Commit `6dfa9c9` passed 117 focused tests plus a real device finalize/read
  smoke. Transaction `voice-wav-finalize-6dfa9c9` is applied and verified.
  The user completed one real short Chinese phrase from red `请说话` through
  `识别中` to insertion in the original field. No new GMU, hangcheck,
  `dma_fence`, Q6APM or service failure appeared. This is a daily-use smoke,
  not a claim of long-term GPU or ASR stability; longer speech and offline
  failure handling now follow real-use bug reporting rather than stress tests.
- Superseded curated-phase state: the daily-use ES-DE library was the 71-entry `精选集`, not the former
  22,670-ROM full import candidate. Live contains 75 ROM files including four
  hidden BIOS/parent support files, 71 metadata entries and 67 media files,
  represented by 155 regular managed files / 2,480,790,465 logical bytes.
  The collection-only stage manifest SHA-256 is
  `6506f7d63ce1e935a900c0ca1509d9e1106819bf0a67c695bee3f893e1a188df` and
  its inventory SHA-256 is
  `86e707adb1da419bdd26e084280e4f78a9a45a20332380c76ffbdebbfb8f2d91`.
- The original 31 GB v2 stage remains untouched: manifest
  `7939c6f985198b6580e2f02fc547ca41320e51c146243ef85f22cac1c392abd8`,
  inventory `0b22badff62c8fa88a3e1f15a4b2c052658f49ebf7cbae7d380e2c8ad8098f98`.
  The pre-import live trees and both ES-DE config files are preserved at
  `/home/pocketds/.local/state/pocketds-linux-kit/backups/esde-curated-v1-20260829.ZstNyC`.
- A real KDE-session ES-DE smoke started in 1,469 ms, logged zero error/fatal or
  parse failure, stopped cleanly, restored `joymouse`, and removed its runtime
  journal. FC, FBNeo Arcade, Neo Geo, MAME 2003 Plus, NDS, PSX and PSP each
  loaded one selected game through the installed core. Normal Wayland PSP
  succeeded; its earlier null-video diagnostic crash is not a daily-use failure.
- The one selected N64 payload is gzip data mislabeled `.z64`; gzip integrity is
  valid but the decompressed header is not an N64 byte-order magic, so ParaLLEl
  N64 exits with `Failed to load ROM`. This affects that payload in the later
  full library as well; replace that ROM when a verified copy is available.
- The first post-import reboot completed in about 40 seconds. KDE/Wayland, both
  DSI displays, Wi-Fi, audio endpoints, brightness restoration, the 155-file
  curated library and all core services returned. ES-DE then started in 1,582 ms,
  stopped cleanly and restored `joymouse` again.
- ES-DE menu visibility, one physical launch/play/exit round and controller
  semantics are now attended smoke passes. Long-term stability is not claimed.
- Superseded historical state: Android had been removed at this point in the
  chronology. See the 2026-09-03 recovery state at the top of this document.
- Device addresses are private runtime state and are intentionally not stored in Git.
- Maintenance SSH is key-only. The IdentityFile and current DHCP address belong
  in the host-private operator state, never in a firmware/release tree. An
  expired ControlMaster socket is not a reason to request a device password.
- Known failure on the current MSM/Adreno kernel: Plasma/Chromium GPU buffers can
  hang in `dma_fence_default_wait`. If that happens, reboot can stall indefinitely
  at `A stop job is running for User Manager for UID 1000 (user@1000.service)`.
- Do not infer an Android boot merely because SSH port 22 is closed during this
  state. The kernel/network can remain alive after sshd has already stopped.
- A 45-minute 165/60 active-development baseline kept boot/display signatures
  stable and journal accounting exact, but observed one fenced-register delay
  and four recoverable KWin D-states (two sampled for about 10 seconds). It is
  not a clean stability run. Collector CPU was 1.556%, above the 24-hour target.
- A separate current-boot history correlation found that all seven recorded GPU
  lockups were preceded by 6--13 `GPU_SET` GMU OOB timeouts, with the first
  failures roughly 3--5 seconds before hangcheck and recovery 1--4 ms after it.
  KWin's graphics-reset notices followed the lockups. Exact source comparison
  identifies downstream `d84f65c`, which enables A740 IFPC and assigns an
  A750/X185-only register list, while official DRM/MSM `msm-next` leaves IFPC
  disabled for the same A740 chip ID. This is a high-confidence hypothesis,
  not an A/B-confirmed fix. The isolated revert has now been built in two
  independent locked buildroots; their 499-entry payloads are identical and
  differ from baseline only in the explained IFPC-derived Image bytes. The
  native-lid candidate boot image is deterministic and content locked, but has
  not been installed.
- Forced Chromium zero-copy flags were removed from `/etc/chromium/chromium.conf`.
- PDS-017 is narrowed to the SM8550 `qcom_battmgr` property path leaving
  `battmgr->unit` at its zero-allocated mWh value while registering charge
  properties. The pinned Qualcomm Android SM8550 protocol maps the same
  firmware IDs to charge. A one-line mAh initialization candidate applies to
  the exact downstream source and must not be combined with the IFPC
  experiment. Isolated Fedora 44/aarch64 completed the driver object plus
  Image, 319 modules and 417 DTB/DTBO targets. The RPM-buildroot-only initramfs
  source was cleared, so this is not a reproducible/installable package; it has
  not been booted. Installed UPower 1.91.3 will use charge units after the fix, but its
  missing-design-voltage fallback uses live `voltage_now`; EnergyFull drift and
  ETA therefore remain runtime gates and ETA stays unknown.
- The default browser is now the isolated Arch Linux ARM V4L2 build of Chromium
  `151.0.7922.137` at `/opt/pocketds-chromium-v4l2-151.0.7922.137`.
  `/usr/local/bin/pocketds-chromium-v4l2` supplies the private compatibility
  libraries plus these conservative flags:
  `--ozone-platform=wayland`, `--force-renderer-accessibility=form-controls`,
  and `--enable-features=AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL`.
- Hardware H.264 decode was verified: the Chromium GPU process opened
  `/dev/video0`, and its log showed `V4L2StatefulVideoDecoder` dequeuing NV12
  frames from the qcom Iris decoder. Do not add a zero-copy feature flag.
- The desktop/menu override remains named `chromium-browser.desktop`, so the
  existing KDE taskbar reference resolves to the V4L2 wrapper. The Fedora RPM
  stays installed only as a rollback binary and is not the default browser.
- Its launcher points directly to the signed package's locked 256px hicolor
  icon and refreshes KDE's service cache; a clean reinstall no longer depends
  on a missing system icon-theme entry or produces a blank launcher.
- The wrapper exports `CHROME_DESKTOP=chromium-browser.desktop`, matching the
  installed launcher ID so KDE can also associate the running window and its
  taskbar icon with the same entry.
- The pre-migration browser profile backup is
  `/home/pocketds/.config/chromium.pre-v4l2-20260826`.
- The Arch Linux ARM Chromium and compatibility packages were verified against
  the official build-system key fingerprint
  `68B3537F39A313B3E574D06777193F152BDBE6A6` before extraction.
- A native Plasma 6 lower-screen control center is installed as the desktop
  widget `org.pocketds.controlpanel.v3` on containment 2 / DSI-2. It polls the
  small native helper `/usr/local/bin/pocketds-panelctl` every two seconds and
  controls fan profiles/manual PWM, TuneD CPU/GPU performance profiles, each display brightness,
  PipeWire volume/mute, and the custom five-row Pocket DS lower-screen
  keyboard. The keyboard button signals `pocketds-keyboard.service`; it no
  longer launches the small Onboard window. The daily machine still retains
  Onboard as a masked/inactive rollback package; the clean `.pds2` release
  candidate no longer depends on or packages its retired integration.
- The live telemetry cache and Panel reader identify DSI-2 as 1024x768 inferred
  physical pixels and 819x614 logical at 1.25 scale, and report
  Wayland/DRM/OpenGL/FD740. Output refresh is explicitly not application FPS;
  app FPS remains unavailable. The formal 24-hour schema-2 report is
  `complete=true` but `accepted=false`: it completed 43,200/43,200 reads with
  43,200 distinct GPU timestamps, zero failures, stale reads or regressions,
  2,097 ms maximum gap and 667 ms maximum sample age. The telemetry service
  remained PID 126678 with zero restarts, -106,496 bytes current-memory growth
  and 0.2323% of one CPU; the reader used 0.0298%. All 43,200 display statuses
  were `ok`, application FPS stayed null, but both 165.0/59.999 and
  120.0/59.999 refresh pairs were observed. Thus `one_refresh_pair` is the only
  false harness gate. Schema 2 does not record the transition or establish why
  the second pair appeared.
  The report is SHA-256
  `c09f635b043bd9e20d1fa3425fafdef98102485cce7e87fd0bc44985912c097e`.
  This is telemetry continuity/resource evidence, not a system-stability PASS.
  A separate read-only journal classification for the same interval counted
  83 GMU `GPU_SET` OOB timeouts, 6 explicit hangchecks, 5 preemption timeouts,
  11 `recover_worker` records, 6 KWin-offender/full-graphics-reset records and
  12 atomic `EBUSY` events. It counted zero HFI, fenced-register, SMMU or runtime
  GPU faults. All 11/11 recovery sequences had a preceding OOB burst; the final
  52,730.946 seconds after journal time 13:20:45 had no new OOB or recovery.
  KWin child PID 1011 remained the same process throughout, while wrapper PID
  1001 had `NRestarts=0`. These categories must not be collapsed into 11
  hangchecks, and the long quiet tail does not erase the earlier resets.
  A later read-only event-order audit found that the final recovery at
  monotonic 57136.773 was followed 255 ms later by a Panel request for
  `pocketds-performance`. The profile stayed active with the GPU governor at
  `performance` and 1 GHz; the next 87,036.532 seconds had no OOB/recovery,
  although one fenced-register delay still occurred. This correlation supports
  the IFPC-transition hypothesis but is not causal proof or a daily workaround:
  pinning maximum GPU frequency costs power, heat and fan noise. Pinned DRM/MSM
  source confirms that the devfreq performance governor does not clear the
  IFPC quirk or change the GMU idle level, so the quiet tail may also be workload
  timing or chance.
  Synthetic-load accuracy and rescued KWin-loss/recovery gates remain open.
  After a controlled
  plasmashell-only reload, the
  lower Panel visibly shows `165/60 Hz`, `Wayland · DRM · OpenGL`, and the GPU
  card while the telemetry PID and cache cadence remain continuous. A later
  exact-output crop proved the apparent large black regions were outside DSI-2.
  Correcting containment 2 from 816x608 to KWin's live 819x614 logical size
  covered all four DSI-2 edges and survived all three shell restart results.
  Two of those three direct restarts ended in a separate roughly 40-second
  plasmashell stop timeout. A separate static, confirmation-gated recovery
  oneshot is now installed: five live requests used KDE's graceful quit and
  restored Plasma and D-Bus ownership in about one second each. The last four
  traversed the exact installed Panel helper action. KWin/keyboard/GPU telemetry
  PIDs remained unchanged with zero restarts and no Pocket DS QML load error.
- The formal IFPC test path is now content bound end to end. Collector headers
  distinguish the same-release baseline/candidate with `/sys/kernel/notes` and
  also hash KWin, both A740/GMU firmware files and the installed smooth fan
  controller. It records the locked `pocketds-performance` TuneD and
  `aggressive` fan profiles in every five-second sample, then rehashes all
  fixed/runtime controls at the end. A private evaluator requires
  baseline/candidate × three boot rounds × dual 165/60, dual 60/60 and
  upper-only 165: 18 reports, nine matched pairs and six distinct boots. It
  rejects report reuse, workload/control/display drift, incomplete telemetry,
  over-85°C GPU temperature, more than 3°C paired thermal regression and all
  candidate GMU/HFI/lockup/fence failures. The frozen offline WebGL2 workload
  binds the signed Chromium V4L2 runtime and 48 compositor layers; its runner
  is plan-only by default and cannot change display, boot, service or power
  state. A dedicated live-preflight mode now exercises every read-only start
  gate without reserving evidence or starting Chromium/the collector; live
  execution still only refuses a profile mismatch and never switches a power
  or fan profile. Even a full pass leaves candidate installation
  unauthorized. These locks make temperature pairs comparable but do not
  replace the separate PWM/RPM/acoustic fan acceptance path.
  The action UI also exposes a KWin-native fullscreen toggle, dynamic current
  InputPlumber mapping help, a confirmed recovery dialog and reversible
  collapse-to-desktop state. Plasma initially clamped live Widget geometry to
  816x608 despite exact stored 819x614 tokens; deriving the painted surface from
  `Plasmoid.screenGeometry` restored runtime 819x614. Every pixel on all four
  edges of the exact 1638x1228 capture crop now matches the Panel background,
  with transparent pixels immediately outside the output. Physical touch
  collapse/reopen, dialog and fullscreen round trips, forced recovery fallback
  and cold-boot geometry remain open.
  The fail-closed dynamic helper matched the live
  819x614 geometry as a no-op dry-run and refused `--apply` while Plasma was
  active without creating state; actual helper apply/restore is still not run.
- v3 is the touch-sized layout: 10 px slider tracks, 32 px handles, 42 px
  slider touch height, 52 px profile buttons, 64 px keyboard button, and
  larger Chinese labels/metrics. During testing the lower slider had only
  reached 64%; DSI-2 is now verified at raw hardware maximum 4080/4080 while
  DSI-1 remains independently controllable.
- The Panel/keyboard polish set is now live through verified transaction
  `panel-keyboard-polish-v1-20260829` at repository revision
  `242539a49d32dd0da6ef790f8831e47b9b9de049` (manifest SHA-256
  `22bb68f2c983afc2231260a29c5e9836c58b4ce1d7453caa7f90d0ee16e5e254`).
  It replaces startup zeroes with explicit waiting/unknown state,
  marks telemetry interrupted after 6.5 seconds, locks stale hardware controls
  while preserving escape actions, labels the four header actions directly,
  adds the same repeatable backspace to the symbol page, gives the F1--F12 row
  a fixed compact height without shrinking the main touch rows, shortens
  transient ASR labels, removes the unresolved fan icon, uses the Breeze
  `code-context` icon for Codex, and baseline-aligns the fan percentage.
  Targeted Mac and Fedora 44/aarch64 tests pass; independent review closed with
  P0/P1/P2 all zero, and Impeccable's one-shot detector returned no findings.
  The live Panel QML hash is
  `6c1d31d29a535a948c945a1abad7adec68aa9297cd0b09fb742247bba181e75e` and
  the live keyboard main hash is
  `4678102cbb502a1cd9a67ed9b44f73b9dea8ea44ff5b78d7872b8a6857ef2cb1`.
  One bounded graceful Plasma reload and one keyboard restart preserved KWin,
  keyboard and GPU telemetry health with zero crash restarts. Exact lower-screen
  screenshots verify the compact function row, unchanged main-key touch area,
  resolved icons and numeric alignment. Physical touch, fullscreen and
  desktop-collapse acceptance still require the user.
- The repository's canonical DNF5 default protection is now the exact ordered
  union of the evaluator's hardware and graphics rings: 22 globs covering
  kernel/Pocket DS/firmware plus Mesa, DRM, GLVND, VA-API/VDPAU, KWin/Plasma,
  Qt/KWayland/Wayland/Xwayland, Xorg and Vulkan. Ordinary Chromium, KDE Connect,
  RetroArch and bash remain outside those globs. The focused installer derives
  the effective value from that one source, and its mock proves a configuration
  missing the final graphics glob fails and restores the old file.
  The 22-pattern drop-in was deployed on 2026-08-29 through the focused rollback
  installer; source/live SHA-256 is
  `c6c4dda8c59290a0d13ce372d3ede9a1435961c8a38dae3cef415d9e568f03fe`.
  DNF5 reports all 22 in exact order with `protect_running_kernel=1`; live is
  root:root 0644 and single-link. The retained previous-file backup is
  `/var/lib/pocketds-linux-kit/backups/20260829-225516-update-protection`.
  No package metadata was refreshed and no package was downloaded or installed.
  This prevents Discover or bare DNF from silently updating either protected
  ring; it does not authorize a blanket update or any stored-transaction replay.
- The ChatGPT RPM repository trust path was repaired on 2026-08-29 without
  disabling either package or repository signature checking. The installed
  `Codex Linux Repository` key fingerprint
  `3BFA0E4AE8B8CC16A2D9BA684A3B4A566C4660E4` is now trusted by RPM and the
  DNF5 repository keyring. A fresh four-repository `check-upgrade` completed
  without a key prompt or signature error and listed no protected package.
  One deliberately narrow ordinary-userspace update then completed as DNF5
  transaction 29: `python3-typing-extensions` 4.15.0-3.fc44 to 4.16.0-4.fc44.
  No other package changed; ChatGPT itself was left untouched.
- Panel performance actions now use the three managed TuneD profiles rather
  than writing a CPU governor independently. A live powersave/balanced/
  performance matrix verified CPU limits, GPU governor/max, fan profile and
  Panel status, then restored `pocketds-balanced`. That was the test cleanup,
  not the current state: a later Panel action selected `pocketds-performance`
  after the final recorded GPU recovery, and the live profile remains there
  pending the controlled IFPC comparison.
- The standalone LayerShell Panel under `experiments/pds005-panel-shell` is a
  disabled source experiment only. It is absent from installers and the
  default `Makefile`, has not been built or installed on the device, and is not
  a replacement. It is not evidence that desktop exit, fullscreen or recovery
  works; current-session geometry was accepted independently on the Plasma widget.
- Do not use `kscreen-doctor` to set DSI-2 brightness on this image. It can
  abort and may store zero brightness. The panel writes the two independent
  kernel backlight devices through `/usr/local/libexec/pocketds-panel-root`.
  KWin output state is now DSI-1=0.56 and DSI-2=0.68; its pre-change backup is
  under `~/.local/state/pocketds-linux-kit/backups/20260828-pds003-kwin-brightness-official/`.
  PowerDevil still globally copied 56% to the lower hardware backlight during
  a controlled restart. The independent daemon recognized that strict equal-
  percentage signature and restored the lower target to 68% on its first
  observation: the 200 ms trace showed the overwrite at 0.801 seconds and the
  restored value by 1.801 seconds, while the upper display remained at 56%.
- The old GTK/Python fan indicator is disabled by the user autostart override
  `~/.config/autostart/pocketds-fancontrol-indicator.desktop`; the actual
  safety-critical `pocketds-fancontrol.service` remains active.
- Before the rebuild, official Codex CLI 0.149.1 (Linux ARM64 standalone
  build) was installed at `~/.local/bin/codex`, and the quota collector had
  verified a live `source=app-server` response. That statement is historical,
  not current: the rebuilt system has neither the binary nor `~/.codex`
  authentication/configuration. The collector and Panel remain deployed but
  now correctly fail closed to `source=unavailable`; restoring live quota
  requires both a freshly verified ARM64 CLI and an attended account login.
- Plasma layout backup before the panel was added:
  `~/.config/plasma-org.kde.plasma.desktop-appletsrc.pre-pocketds-panel-v1`.
- The retired wxime/WXASR directory client has been removed from the candidate
  keyboard chain. That source revision preferred the private HTTPS transcription backend used at that historical revision,
  with WebPKI hostname verification, a 75-second total deadline, bounded
  502/503/network retries and strict response framing. Credentials are created
  only by a hidden-input, crash-recoverable provisioner and never enter argv,
  env, Git or reports. The disposable HTTPS worker receives them through a
  private pipe only after disabling dumps and installing no-new-privileges,
  RLIMIT_NPROC and seccomp process-creation confinement. Its credential lock is
  held through every request/retry and cancellation performs TERM→KILL→reap.
  A seven-file durable transaction binds file and user-unit activation/rollback;
  active runtimes without a complete restart preimage fail before mutation.
  The final independent candidate audit is GO with 130/130 aarch64 tests and no
  P0/P1. The seven-file `asr-runtime-20260829` transaction is now APPLIED and
  VERIFY-complete on the Pocket DS; the activated keyboard stayed running with
  zero restarts and retained the fixed 820x615 lower-screen geometry. The
  system UCM regression is now repaired: PipeWire exposes the built-in
  `HiFi__Mic__source` from card 0, device 2 and makes it the default input while
  retaining the Speaker sink. The user verified ChatGPT's built-in voice input
  end to end; this is a locked PASS and its launcher and permission path are
  not keyboard-diagnosis targets. One user-triggered keyboard `parecord`
  session also produced a valid WAV and reached cloud recognition. Its later
  editable-target rejection was fixed separately. These observations prove
  basic physical capture, but not acoustic/channel quality, privacy, repeated
  cold starts, reboot/resume or keyboard-ASR reliability.
  Fedora `pipewire-utils-1.6.8-1.fc44.aarch64` is installed for the bounded
  native graph audit; installing it did not restart or change the audio graph.
- The local fallbacks remain sherpa-onnx/SenseVoice int8 and whisper.cpp, in
  that order after the historical private backend. SenseVoice requires four owned, single-link,
  non-writable and
  bounded external artifacts: binary, adjacent `libonnxruntime.so`, int8 model
  and tokens. Fixed Chinese+ITN argv accepts only one bounded stdout JSON speech
  object. The official v1.13.2 aarch64 shared archive and 2024-07-17 model
  archive were downloaded only to a private Mac audit directory, matched their
  published GitHub digests, had no linked/special/unsafe tar members and passed
  a five-round Fedora 44/aarch64 smoke from the minimal two-file engine layout.
  The real prepare path then installed all seven locked runtime/license files in
  a disposable Fedora root, passed the same smoke, and reported 7/7 unchanged on
  the next plan before the entire root was removed.
  Git stores only the exact lock, complete license snapshots and a network-free,
  confirmation-gated installer. These optional local fallback artifacts are not
  installed on the Pocket DS, so their live quality and resource use remain
  unmeasured.
- The keyboard runtime is now `Type=notify` and owns visibility through one
  state adapter. Fifty real Panel show/hide cycles crossed the 900 ms focus
  recheck with the same PID and zero restarts. An isolated Chromium page-entry
  probe now passes 5/5 automatic show/stable/hide rounds; a defunct-proxy
  identity bug found by the first run is covered by a 500 ms liveness check,
  and close-to-hide measured 0.375--0.470 seconds with the same PID and zero
  restarts. Konsole single-round auto-show passed; isolated XWayland reconnect
  remains an acceptance item. Voice input no longer changes numeric ALSA
  controls and refuses speaker-monitor capture. Keyboard capture can still hit
  an intermittent Q6APM `APM_CMD_GRAPH_START` timeout and ASoC `-110`. A first
  bounded userspace retry candidate was rejected because its failure paths
  could block the UI and authorize cloud upload after the editable target had
  changed. The v4 replacement state machine then passed an independent review
  with no P0/P1/P2 findings plus 106 keyboard, 37 ASR and 9 voice-artifact
  tests. Commit `bce1901` was staged, applied and verified through the six-file
  UI transaction `keyboard-asr-retry-v4-20260829`; only the three changed
  keyboard files drifted, all six live targets now match, and the restarted
  keyboard service is active with zero crash restarts. Its conservative six
  second Q6APM readiness window, one natural nonzero-exit retry and cancellation
  behavior still require a user-triggered attended recording; no automatic
  recording was run during deployment.
  The only later keyboard evidence is one `12:38:11` cancellation while the
  voice session was still `CONNECTING`: the former toggle handler treated a
  second activation as an explicit cancel. That event cannot distinguish a
  deliberate second tap from a duplicated toolkit activation, so the bounded
  fix makes `CONNECTING`, `STOPPING`, `RECOGNIZING` and `PENDING_PASTE`
  activations idempotent while preserving the one intentional stop action in
  `RECORDING`. Repeated ignored activations emit at most one privacy-safe log
  per generation/state. The candidate is commits `c5d12f0` and `64acb62` on
  `fix/keyboard-asr-activation-idempotence-v2`; 111 keyboard, 37 ASR,
  9 voice-artifact, 15 provisioner and 17 transaction tests plus geometry,
  visibility, UI design, lint and diff checks pass. Six-file transaction
  `keyboard-asr-idempotence-v2-20260829` then staged the clean revision and all
  six exact preimages, applied and verified all targets with manifest SHA-256
  `8988bb37d3b33765d890f20ac75ba73f9d0d5d583425b78ed01aa8e441227b7d`.
  Only keyboard main differed in the read-only plan; its live/source SHA-256 is
  now `631a07c50b6c879f847c84dfaf9bf5018470f9208ad1db0fcc33aca9cdedd971`.
  One keyboard restart changed PID 527927 to 548490; it remains active with
  zero crash restarts while KWin PID 1001 and GPU telemetry PID 126678 were
  preserved. No recording was started during diagnosis, tests or deployment.
  ChatGPT voice remains a user-verified locked PASS and was not changed. The
  duplicate-activation and successful-insertion behaviors still need the
  user's attended touch test.
- Before the Wiliwili session-stack update, the deployed joymouse generation
  completed fifty exact gamepad/joymouse round trips without an InputPlumber
  restart. Recreating the virtual keyboard target cleared the stranded
  synthetic Alt state, and the held-modifier observer reported an empty set.
  The live joymouse mapping remains R1/RB to left click, R2/RT to right click,
  and LB/LT to scroll; its physical press, release and chatter acceptance
  remains attended.
- The current game-input stack was deployed from clean device revision
  `b3bf26d188cc590b92590b83932c5b22fdf05ac8` by transaction
  `20260829-181740-fbde0e12047eb9bc`. It matches the live InputPlumber target
  identity `xbox-elite:Microsoft X-Box One Elite pad` rather than the rejected
  generic `gamepad` type. Three benign managed live cycles completed
  `joymouse → gamepad → joymouse`; InputPlumber remained PID 566 in the same
  invocation with zero restarts. The independently reviewed passive attended
  harness binds the exact managed process, Flatpak instance, lease, unique
  direct-virtual event FD and source/live runtime without reading the event
  queue, grabbing, writing or signalling. Its 60-second response-window
  follow-up is device commit `e1278ab8f292bdaeb3ef3fd0a56047bbacb1517b`;
  30/30 focused tests and device lint pass with a clean repository. The first
  attended A sample used the former 10-second window and timed out before
  physical input, so it is a recorded FAIL rather than button evidence.
  Wiliwili was then closed through its UI; exact live postflight observed the
  Wiliwili process/service absent, mode `joymouse` and the real
  `$XDG_RUNTIME_DIR/pocketds-game-session/session.json` absent. Lifecycle
  restoration is verified, while A/B/X/Y, D-pad, sticks, shoulders, triggers
  and Guide semantics still require attended validation.
- The verified ES-DE v2 stage remains untouched: 14 systems, 22,670 ROM and
  gamelist entries, 21,189 media files, 71 curated entries, and 43,874 regular
  files / 31,122,917,449 logical bytes. Live still has zero gamelists. The
  isolated `feat/pds012-esde-import-v1` candidate is uncommitted, undeployed
  and NO-GO. Its rollback-fence and real-SIGKILL fixes pass directed tests, but
  independent review proved deterministic root/user temp cleanup can delete a
  same-name foreign regular file; root can also unlink the symlink itself and
  user cleanup has stat-to-unlink TOCTOU. Replace it with transaction-scoped,
  unguessable, durably source-bound temp identity and obtain a fresh GO before
  touching stage or live.
- Speaker and DisplayPort UCM files match Git. The WSA MM1 control's second
  advertised boolean is not writable on this machine driver (`0,1` and `1,1`
  both read back with member 2 off), so it is recorded as frontend route `1,0`,
  not mislabeled as left/right audio. The historical working route was recovered
  from commit `bbeb1ea`: MultiMedia3 plus the official Qualcomm DMIC0 sequence;
  Tencent/WXASR merely consumed the WAV. The unstable numeric-control code was
  removed without first adding a UCM Mic device, which caused the regression.
  The live system now uses named controls, the official DMIC0+DMIC1 sequences,
  raw `CaptureChannels 2` and `hw:SM8550APS,2`. An isolated ACP probe rejects
  the invalid one-channel draft and accepts `HiFi (Mic, Speaker)`; live PipeWire
  exposes `alsa_input.platform-sound.HiFi__Mic__source` as the default while
  all audio services remain active with zero restarts. Real ChatGPT voice and
  one keyboard WAV prove basic capture, but signal quality, channel
  orientation, privacy, repeated cold start and resume gates remain open. A
  distinct-release Q6APM kernel candidate was built twice with identical
  runtime payloads and independently passed offline-integrity review. It has
  not been installed, booted or hot-swapped; independent fastboot RAM recovery
  is unverified, so runtime and deployment remain NO-GO.
- The development image is not releasable: a vendor RPM supplies global
  `%wheel ... NOPASSWD: ALL`, root licensing/provenance/SBOM material is
  incomplete, and wallpaper redistribution evidence is missing. The isolated
  Chromium layer now has a pinned receipt for Chromium plus four compatibility
  packages: all five exact archives and detached signatures passed the official
  Arch Linux ARM build-key check, their package metadata passed, and all 58
  locked payload entries matched. `release_ready=true` in this lock is scoped
  only to that Chromium layer; it is not a firmware release verdict. These are
  PDS-020 release facts, not instructions to remove recovery access in place.
  A read-only mounted-root auditor now
  gates sudoers safety, required license/SBOM/source/asset evidence, Chromium
  integrity versus provenance, private user state and cloned system identity.
  Its live-development-root run failed closed with mode-0600 evidence: sudoers
  file safety passed, while one broad/unexpected passwordless rule, missing
  release manifests, daily user data and cloned machine/network identity
  remained blockers. A post-provenance rerun now confirms Chromium integrity
  and provenance PASS with zero gaps; the overall result still correctly exits
  1 with nine non-Chromium blocker categories. The broad rule is owned by the
  otherwise required `pocketds-userspace` RPM. Its exact signed COPR source RPM
  is now locked together with the complete flat 67-file/148,381-byte extracted
  source manifest, and a deterministic `.pds1` spec transform removes only that
  global rule while refusing any added, missing or content-changed source. The
  transform now also verifies the
  original `Source70` and emits a repository copy byte-identical to the live
  smooth fan controller only as a pair with the new spec. A second read-only
  pre-sign gate verifies exact binary identity, bounded header/newc content,
  scriptlets and the exact installed fan-controller hash, including absence of
  broad sudo rules. Two independent clean Fedora 44/aarch64 offline mock runs
  now produced byte-identical mock-emitted source RPMs, binary RPMs and
  buildroot package manifests. The locked binary SHA-256 is `cd60625f…`, and
  its 71-entry payload manifest is `fdd27a68…`; an eight-case read-only
  reproduction gate passed the real result pair. This is pre-sign evidence,
  not a signed builder attestation: no hardened RPM has been signed, installed,
  upgrade-tested or admitted to a mounted release root. An isolated post-sign
  gate now has ten fixture/static cases: it imports only a receipt-bound
  public certificate into an ephemeral keyring, requires exactly one matching
  Header OpenPGP signature with no legacy signature, then repeats the locked
  header/payload audit. A dedicated `.pds2` entry point binds its own source/
  build locks and 60-entry payload instead of the historical `.pds1` inputs.
  There is no real signed artifact yet, and the gate cannot sign, generate
  keys, build, install or download.
  The vendor baseline was also reproduced twice with identical binary/source
  RPMs and buildroot package manifests. A confirmation-gated offline mock
  transaction then passed vendor install, hardened upgrade, vendor rollback and
  hardened re-upgrade. Both hardened stages removed the broad sudoers file,
  installed the locked smooth controller and kept one package identity; the
  generated root was cleaned. This intentionally used `--nodeps --noscripts
  --notriggers`, so dependency resolution, scriptlet behavior, SELinux and live
  services were outside that historical `.pds1` test. A new exact `.pds2`
  transform removes the inactive Onboard stack that Fedora 44 official repos
  cannot resolve. Two clean offline builds now reproduce the same 56,579-byte
  binary (`457ee254…`) and 60-path payload (`e7d14b81…`). A separate exact
  81,798,562-byte private archive holds 135 Fedora dependency RPMs, the Fedora
  44 key and the project RPM. An offline read-only gate
  checks every file/payload hash and NEVRA, all 135 Fedora signatures in an
  isolated keyring, and the unsigned project's independent payload audit. The
  confirmation-gated full mock transaction now initializes offline and installs
  all 136 local RPMs with normal dependency handling and scriptlets/triggers,
  then removes/reinstalls offline with locked package counts
  `187 → 323 → 322 → 323`; both installed states had no legacy OSK or global
  sudo files, and the root was cleaned. The 186 non-key baseline RPMs were then
  added to a complete 322-RPM/151,701,195-byte target archive. All 321 Fedora
  signatures passed; a repository-disabled/cache-only DNF5 transaction installed
  only those local RPMs into an empty root, matched manifest `7cdf60c9…`, passed
  `dnf check` and the payload contract, and removed the root. This still leaves
  project signing, signed repository metadata, SELinux-label, image-service and
  mounted-root gates open. Two fixed-revision, single-worker Fedora
  `createrepo_c` runs now also produce byte-identical four-file metadata;
  primary/filelists/other all bind the exact 322-package set and repomd is
  `c45ef780…`. Detached repomd and project RPM signatures remain false, and the
  daily device was not changed. A ten-case final-repository receipt verifier now
  rejects signing this unsigned metadata directly: `.pds2` must pass post-sign
  first, only its archive member may change, two new metadata results must bind
  that signed container, and both must carry the same detached signature. Real
  key/signatures and a real DNF5 `repo_gpgcheck` smoke remain absent. The smoke
  harness itself now has ten passing fixture/static cases. It enables only a
  disposable local `pds2` repository, requires both metadata and package GPG
  checks plus `skip_if_unavailable=0`, matches all 322 available identities,
  installs their exact NEVRAs into an empty root with normal scriptlets and
  triggers, runs `dnf check` and the payload contract, rechecks evidence drift
  and always cleans. Its private report binds the final receipt, signed RPM,
  repomd, detached signature and archive-manifest hashes; no real signed input
  exists yet, so this remains NOT RUN rather than PASS.
  Reports expose only fixed categories, never discovered paths or account
  names. A clean offline osbuild composition, not sanitizing this daily machine,
  is the selected release path.
  That path now has a fail-closed staging contract: Fedora 44/aarch64
  `generic-container` is pinned only as a non-flashable, no-boot/no-partition
  rootfs artifact. The stale `.pds1` userspace template pin has been corrected
  to the current `.pds2` line. Its repository gate now rejects generic evidence
  placeholders and accepts only the complete DNF5 smoke report contract. Its
  release-evidence gate now binds the five repository evidence files, an
  all-pass 11-check offline mounted-root audit and the same SBOM's fixed official
  Schema/OWL/SHACL report; rootfs completion likewise requires the structured
  final-root report bound to the stable composition policy, locks, package,
  SELinux and audit results. Generic receipts fail. Nineteen fixture/static cases
  pass. The checked-in blueprint remains intentionally
  blocked until exact signed project/userspace RPMs, an immutable signed
  repository snapshot, release evidence, builder verification and rootfs smoke
  evidence exist. It cannot embed accounts, credentials, cloned identity,
  network profiles, repository URLs, disk wipes or boot-chain changes.
  A dedicated final-root smoke now has ten passing fixture/static cases. It
  is confirmation-gated and read-only, rejects the live root, binds the osbuild
  tar and all relevant locks, checks the exact 322-package RPMDB, hardened
  payload/service/legacy contract, SELinux labels and all 11 mounted-root checks
  against one offline root, then rechecks input stability. No final artifact
  exists, so the real smoke remains NOT RUN; its exact report contract is
  already wired into the still-false composition gate.
  SPDX 3.0.1 validation now has a pinned, offline two-layer implementation:
  eleven fixture/static cases pass, and the official package SBOM example
  passes JSON Schema plus local JSON-LD/OWL/SHACL on Fedora 44/aarch64 with the
  exact four validator package identities. The three official materials are
  size/hash locked; the official schema's two identical duplicate members have
  a narrow whole-artifact-bound exception while every project input remains
  strict JSON. No final-composition SBOM exists, and validation deliberately
  makes no license, copyright or redistribution conclusion.
  The mounted-root bounded preflight now consumes the same official flattened
  JSON-LD shape rather than its former embedded-object fixture: graph IDs,
  creation references, document/SBOM roots, complete package membership and
  `software_*` profile fields must all be coherent. Eighteen release-root
  fixture/static cases pass. The five-node fixture also passes both pinned
  official layers in isolated Fedora 44/aarch64 (26 RDF triples); this still
  does not make it a final-composition SBOM.
  The tracked-source inventory is now schema v2: its release-evidence map names
  the authoritative nested `components/assets/ASSETS.json` instead of the
  nonexistent root alias. Six fixture/static cases pass and the report remains
  strictly factual/non-authorizing. Clean Mac/device trees both measured 374
  files and 7,240,185 bytes with identical canonical files-array hash
  `28cd37c2…`; all five required release-evidence paths remain absent.
  The two wallpapers now have independently repeatable trusted C2PA evidence:
  exact image hashes, OpenAI Media Service/GPT Image creation claims, claim and
  data-hash validation, pinned c2patool and official signer/TSA trust lists.
  Timestamp-chain trust remains an explicit tool limitation, and C2PA is not
  treated as a license or redistribution permission; the asset release blocker
  therefore stays open while the personal installed wallpapers remain intact.
  The release auditor also accepts an explicit asset-free composition, but only
  when its manifest array and actual asset directory are both empty; this adds
  a public path without weakening or deleting the daily-device copies.
  The installer now exposes the same boundary as non-ambiguous CLI profiles.
  Daily installs default to `personal-assets` and verify the exact images; an
  asset-free request against this personal source tree fails before any backup,
  compilation, sudo or install action.
- The lid sensor is a Rohm BU52053NVX on active-low TLMM GPIO 166. The Pocket
  DS device tree now exposes it through `gpio-keys` as `EV_SW/SW_LID`, with
  50 ms debounce and `wakeup-source`. GPIO 166 maps to the SM8550 PDC wake IRQ
  116, and `wakeup-event-action = <EV_ACT_DEASSERTED>` makes only lid-open wake
  the system. This replaces the former userspace gpiomon/uinput bridge.
- The built-in AYANEO keyboard and controller are behind the Renesas
  `1912:0014` xHCI controller. Udev rule `71-pocketds-usb-wakeup.rules` disables
  that controller as a wake source after `xhci-pci-renesas` binds; the earlier
  PCI-add rule ran before `usb_hcd_pci_probe()` re-enabled wake. A strict helper
  applies the same policy during installation. This prevents internal HID from
  racing suspend with `-EBUSY`; GPIO lid and PMIC power key remain wake sources.
- The native lid DTB exposes `EV_SW/SW_LID`. Deep suspend has completed and
  resumed successfully multiple times, but s2idle can stop after the kernel's
  `PM: suspend entry` message and require a hard reboot. PowerDevil and logind
  therefore ignore lid-close events, avoiding the password lock-screen path.
- The running Android bootimg v0 is now structurally measured: all three boot
  aliases match, its gzip payload expands to the signed COPR raw `Image`, and
  its appended DTB matches the versioned `/boot/dtb-*` file. The boot image is
  intentionally not the unmodified RPM payload: it contains the native-lid
  DTB. A source patch against the exact signed SRPM tree reproduces that DTB
  byte-for-byte. An isolated Fedora 44/aarch64 build with the historical GCC
  16.1.1 toolchain now also reproduces all 496 RPM-declared payload entries;
  the normalized extraction contains 499 entries after its three implicit
  parent directories are included:
  341 regular files, 156 directories and two symlinks share normalized tree
  SHA-256 `4654c0aa...`; raw Image, Android bootimg, System.map, all 323
  modules, config and both DTBs are byte-identical. The unsigned RPM container
  itself is deliberately reported non-identical because its header/signature
  bytes differ. The exact currently booted native-lid composition is now locked
  as rollback artifact `pds002-live-baseline-rollback-v2`: its gzip stream
  reproduces the signed raw Image and its appended DTB is bound to the exact
  source patch and independent DTB rebuild. Static preflight passes, but real
  rollback boot/recovery and an independent recovery path remain untested, so
  the artifact is not yet prevalidated and no candidate installation is allowed.
  The IFPC candidate now also has a deterministic runtime composition: its exact
  candidate gzip stream is combined with that same native-lid DTB, then the
  Android v0 kernel size and image ID are recomputed. An existing image and an
  independent tool-created image are byte-identical at 17,090,560 bytes and
  SHA-256 `b29b7c34...`. This closes the candidate composition gap only; the
  tool and report still keep rollback validation and installation authorization
  false.
  The independent recovery design is now bound to the actual boot chain rather
  than a PC-style UEFI assumption. This machine has no EFI runtime and no
  systemd-boot entries; ROCKNIX ABL v1.1.8 loads the FAT `\boot\Image` path.
  Both live ABL slot prefixes match the official 258,048-byte SM8550 payload.
  A private Mac has the locked baseline/candidate images and official fastboot
  36.0.2. Host preflight passes. The supervised dispatcher can only issue a
  temporary `fastboot boot` after one-device and unlocked checks, and the
  runtime verifier uses `/sys/kernel/notes` build ID to prove baseline RAM boot
  while candidate bytes remain on disk. The live fastboot and restore sequence
  is not run yet, so recovery is still not prevalidated. The offline aggregate
  gate additionally requires the RAM and restored observations to share one
  boot token and consistent boot-start time, followed by a later new-token
  normal baseline boot; it cannot authorize the candidate installation. A
  fixed-root three-alias transaction is prepared and fixture-tested for staging
  and exact restoration, including post-rename failure rollback and a durable
  pre-write intent, but it has not been run against live `/boot`.
  The power button is ignored by both logind and PowerDevil; it cannot enter
  suspend or start an authentication-dependent lock path.
- Device suspend callbacks are serialized through
  `/etc/tmpfiles.d/80-pocketds-pm.conf`. With asynchronous callbacks enabled,
  the Goodix GT911 can race its Qualcomm I2C parent, return `-EAGAIN`, abort
  both deep and s2idle, and immediately light the lock screen again.
- Manual suspend is restricted to `mem` + `deep`; the normal lid and power-key
  paths do not request suspend. The former custom power-button service was
  removed because restarting logind in a live graphical session invalidated
  KWin's seat/DRM access and could start a competing Plasma Login session.
- Automatic and resume locking are disabled with `Autolock=false` and
  `LockOnResume=false`. The lock-screen keyboard is not reliable on this Plasma
  build, and both hardware actions are currently ignored, so neither can enter
  an authentication-dependent path.
- The experimental lock-screen QML is no longer part of the default installer.
  The real device was restored to the RPM-owned Plasma 6.7.4 file after its
  package SHA-256 was matched against the saved pre-change copy. The prototype
  remains quarantined for later isolated testing.
- Firewalld no longer activates the stock FedoraWorkstation zone, whose
  `samba-client` service requires a NetBIOS conntrack helper absent from this
  kernel. Unknown networks use public; only the repository-managed
  `pocketds-home` zone permits KDE Connect. The current Wi-Fi trust binding is
  private machine state and is intentionally not stored in Git.
- `NetworkManager-wait-online.service` uses the Fedora vendor command and
  60-second startup-complete timeout again. The former five-second override did
  not make networking faster; it forced `nm-online -s` to fail on every boot.
  The wait runs outside the graphical startup critical path, and its only
  current consumer is a package-cache timer that starts much later.
