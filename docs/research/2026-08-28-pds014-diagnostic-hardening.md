# PDS-014 diagnostic evidence hardening

Date: 2026-08-28. Scope: read-only checks in the current boot. No reboot,
suspend, display/service restart, profile change or network mutation was made.

## Closed defects

The old collector used a seconds-only directory name with `mkdir -p`, so two
runs could mix evidence and replace an archive. It also let tar retain the local
account owner and wrote an absolute archive path into the detached checksum.
Individual captures had no explicit time or file-size bound, and redaction used
path-based check/read/write operations vulnerable to link replacement.

The collector now:

- captures into a unique 0700 `mktemp` directory with 20-second/8 MiB per-file
  bounds and a 32 MiB aggregate bound;
- opens each redaction input once with `O_NOFOLLOW`, requires one regular link,
  bounds the input/output, and rewrites through the same descriptor;
- redacts home paths, valid IP/MAC/UUID/machine IDs, whole serial values, the
  local hostname and non-root username;
- rejects links, oversized content and any pre-existing publication target;
- publishes only after redaction and content hashing, fixes tar metadata to
  numeric owner/group 0 and mtime 0, and records only the archive basename in
  its detached checksum.

Fixture/adversarial coverage includes unexpected encodings, serial values with
spaces, local identities, symlinks, hardlinks, oversized sparse files, capture
bounds and no-clobber/static publication constraints.

## Current-boot device evidence

Source commits: local `8496a3d`; device `f463594`.

Generated ignored evidence:
`diagnostics/pocketds-diagnostic-20260828-072055.hwSQrGX4.tar.gz` (11,492 bytes,
mode 0600). The unpacked directory is 0700 and every file is 0600.

- `make test`: PASS;
- `make test-hardware`: 0 failures, one already-known warning because devfreq
  lacks native busy/total counters (DRM fdinfo remains the GPU source);
- `make test-suspend`: PASS and explicitly reports that no suspend was attempted;
- internal `contents.sha256`: PASS;
- detached archive checksum: PASS with basename-only target;
- tar numeric owner/group: `0/0`;
- a second full redactor scan of every emitted text file: PASS.

PDS-014 remains VERIFICATION. Its outstanding gate is to repeat this matrix and
save a new bundle after the next deliberately scheduled clean boot.

## Gated post-boot orchestrator

The repeat is now encoded by `pds014-post-boot-acceptance.py` instead of relying
on terminal history. Its default invocation only lists the fixed plan. Running
the plan requires all of the following: `--execute`, the exact confirmation
`POCKETDS-POST-BOOT-READONLY`, a new report path, and uptime no greater than 900
seconds (configurable only from 300 to 1800 seconds).

The fixed plan records a clean 40-character repository revision, snapshots the
three system and four user services, runs `make test`, `make test-hardware`, the
preflight-only `make test-suspend`, and `make diagnostic-bundle`, then snapshots
the services again. Completion requires the same positive PID and zero restart
count before/after for every service, unchanged boot identity, monotonic uptime,
the same revision and empty repository status before/after, every step at exit
zero, and confirmation that the local diagnostic bundle was created. There is
no reboot, poweroff, suspend/
hibernate execution flag, package installation or network command in the plan.

Subprocesses have individual 10--240 second timeouts, their process groups are
terminated on timeout/cancel, and captured stdout/stderr is capped for internal
evaluation. Raw command output, boot ID, diagnostic path, hostname and username
are omitted from the final report; it only creates a new 0600 file and refuses
an existing/linked target or linked parent. Nine fixture/static tests cover the
gates, service snapshot ambiguity, repository drift, failures and report safety.

The future deliberate-boot command is:

```sh
CONFIRM=POCKETDS-POST-BOOT-READONLY \
OUTPUT="$HOME/post-boot-acceptance-NEW.json" \
make accept-post-boot
```

It was not executed in this batch because the 24-hour telemetry soak must not
be interrupted or relabeled as a fresh cold boot.

Source commits are local `70d1274` and device `f59f982`. Default plan preview,
the nine targeted tests and full `make test` passed on both hosts. The device
repository remained clean and the telemetry soak remained PID 162791 with zero
restarts. Neither host used `--execute`; no new bundle or post-boot report was
created and the current long-running boot was not counted as cold-boot evidence.
