# PDS-005/PDS-011 exact UI update transaction

Date: 2026-08-28. The live deployment audit reduced the pending update to six
direct-copy artifacts. The already-current Panel metadata, recovery unit and
program, keyboard visibility/unit, telemetry and compiled `panelctl` are not in
this transaction.

`pds005-ui-update-transaction.py` has four deliberately separate phases:

1. the default plan is read-only and reports only logical IDs, states and hashes;
2. stage requires a clean 40-character Git revision and a new private 0700
   transaction directory, then captures 0600 payloads and exact live preimages;
3. apply rejects source/payload/backup/preimage drift, atomically replaces only
   the six fixed targets, verifies every hash/mode/owner, and automatically
   restores already-changed targets when any later step fails;
4. explicit rollback requires the applied marker and refuses to overwrite any
   post-apply content that no longer matches the staged payload.

The one privileged target is the existing root-owned Panel helper. It must have
a safe preimage, is installed through fixed argv without a shell, and therefore
always has rollback bytes. User targets use same-directory exclusive temporary
files, rename and directory fsync. Missing user geometry/voice modules are
removed again on rollback; the adapter's original 0755 mode is preserved in its
preimage even though apply corrects it to 0644.

Stage, apply and rollback use three different exact confirmations. No phase
contains service, Plasma, KWin, keyboard, telemetry or power activation. Eight
fixture/static tests cover the success path, privacy and no-clobber properties,
unsafe source/root preimage rejection, source mode drift, stale live/payload
refusal, post-apply edit refusal, and a synthetic error after rename that must
restore every changed target.

Live stage/apply remains deferred until the 24-hour telemetry soak completes.
After apply, the independent source-versus-installed audit must show all twelve
artifacts `MATCH` with a current compiled candidate. Activation and physical
touch/screenshot tests are separate, so deployment cannot silently reset the
desktop or contaminate the soak.

The implementation commits are local `cd9b307` and device `03725fc`. All eight
targeted tests and full `make test` passed on both hosts. The device default plan
was read-only and exactly reproduced the measured surface: Panel QML, root
helper and keyboard main are `DRIFT`; keyboard geometry and voice artifacts are
`MISSING`; the byte-identical adapter is `MODE_DRIFT`. It reported
`safe_to_stage=true`, `activation_deferred=true` and `services_restarted=false`.
No transaction state directory was created. The telemetry soak remained the
same PID and invocation with zero restarts after this check.
