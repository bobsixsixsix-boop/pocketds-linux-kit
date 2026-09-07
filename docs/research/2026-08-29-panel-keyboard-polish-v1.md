# Panel and keyboard polish v1 — 2026-08-29

## Scope

This bounded UI-only change makes the lower display easier to scan and keeps
the keyboard geometry stable. It does not change the ASR state machine, audio
routing, ChatGPT launcher or permissions, KWin, GPU telemetry, fan policy, or
hardware controls.

The frozen revision is `242539a49d32dd0da6ef790f8831e47b9b9de049`.
Independent review closed with P0=0, P1=0 and P2=0. The Impeccable detector was
run once for the batch and returned no findings.

## Changes

- Header actions use 13 px labels within the existing 60 by 48 touch targets.
- The Codex metric uses the Breeze `code-context` icon.
- The unresolved `sensors-fan` icon slot is removed.
- Fan value and percent use the same numeric family and baseline alignment.
- Only F1--F12 opt out of vertical expansion. Main keys, action keys and the
  bottom row retain their existing expansion and minimum-height contracts.
- Transient keyboard voice labels are shortened without changing state,
  cancellation, focus-target revalidation, capture, recognition or paste.

Mac and Fedora 44/aarch64 targeted suites passed: 16 UI design-contract tests,
106 keyboard tests, 37 ASR runtime tests, 9 voice-artifact tests and the existing
geometry, visibility, Panel-action, routing and deployment checks. The complete
device suite still stops at the same three PDS-018 audio fixture assumptions as
the untouched parent revision; those failures are unrelated physical-mic test
debt and are not changed by this UI branch.

## Live rollout

Transaction `panel-keyboard-polish-v1-20260829` staged and applied six measured
UI files from the clean frozen revision. Its manifest SHA-256 is
`22bb68f2c983afc2231260a29c5e9836c58b4ce1d7453caa7f90d0ee16e5e254`.
All six targets verified; rollback preimages remain retained. Only Panel QML
and keyboard main differed from the prior live set.

The keyboard was restarted once and remained active with zero crash restarts.
The bounded Plasma recovery worker took KDE's graceful quit path, started Plasma
once, restored D-Bus ownership, and preserved keyboard and GPU telemetry process
identity. It did not restart KWin.

Live hashes:

- Panel QML: `6c1d31d29a535a948c945a1abad7adec68aa9297cd0b09fb742247bba181e75e`
- keyboard main: `4678102cbb502a1cd9a67ed9b44f73b9dea8ea44ff5b78d7872b8a6857ef2cb1`

One exact lower-screen screenshot pass verified the compact function row,
unchanged main-key area, resolved icon slots and fan numeric alignment. There
were no Pocket DS Panel QML load errors. Physical touch, transient ASR-state
labels, fullscreen and desktop-collapse round trips remain user-attended gates.
