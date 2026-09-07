# Q6APM candidate recovery gate — 2026-08-29

## Decision

The Q6APM kernel candidate is **NO-GO for installation or boot**. This audit
was read-only: it did not write the device, change a service, record audio,
reboot, enter fastboot or flash a partition.

The running and on-disk baseline remains internally consistent:

- running release:
  `7.1.0-100.20260730172655.pocketds.fc44.aarch64`;
- running kernel Build ID: `8669714a...`;
- `/boot/Image`, `/boot/boot/Image` and the baseline versioned Image all have
  SHA-256 `e4e0a38a...`;
- the baseline module tree, raw Image and modular `snd-q6apm.ko` are present;
- the private host rollback image is readable and also has SHA-256
  `e4e0a38a...`;
- both device ABL slots match the locked official ROCKNIX v1.1.8 ABL prefix;
- the locked Mac fastboot 36.0.2 binary is present and hash-matched.

Those facts prove artifact identity, not recovery. The current recovery lock
still records zero successful recovery boots, zero successful recovery tests
and no independent recovery path. No dispatch, RAM-boot attestation, restore
attestation, normal-boot attestation or final recovery validation exists.

## Why the older IFPC recovery path cannot be reused

The existing rollback, recovery and runtime-boot locks belong to the older
IFPC experiment. They assume baseline and candidate share one kernel release.
Q6APM changes a loadable module, so its persistent candidate must have its own
release and `/lib/modules/<release>` tree. Reusing the older transaction could
select an Image whose modules do not match, overwrite the baseline test
variable or produce a false rollback result.

The old IFPC candidate file on the host is now a compressed, dataless macOS
placeholder and cannot be read completely. It must be retired from the active
Q6APM recovery chain rather than repaired in place. Its locks and reports may
remain as historical evidence. A future IFPC experiment must rematerialize or
rebuild its artifacts in a new private directory and run a fresh preflight.

## Required order before a runtime A/B

1. Build the two-patch Q6APM candidate from clean source at the pinned commit
   with a distinct kernel release. Build the complete Image, modules, DTBs and
   RPM payload twice independently. Bind the source, ordered patches, spec,
   config, toolchain, initramfs, native-lid DTB, raw Image, boot image, Build
   ID, complete module manifest, `snd-q6apm.ko` hash and vermagic in a new
   candidate lock.
2. Create Q6APM-specific runtime and recovery locks, confirmation strings and
   artifact IDs. Re-read and hash every baseline host artifact during the
   dispatch preflight. Reject links, sparse/dataless placeholders, missing
   bytes, changed hashes and overly broad permissions.
3. Install only the distinct candidate module tree and versioned boot image.
   Its RPM scriptlets must not touch `/boot/Image` or `/boot/boot/Image`.
   Verify that the baseline module tree, baseline versioned Image and both
   active ABL aliases are byte-for-byte unchanged.
4. With the user physically present, first perform a baseline-only fastboot
   transport probe: require exactly one unlocked device and permit only
   `fastboot boot <locked-baseline-image>`. No partition or flash command is
   allowed. This proves transport only, not independent recovery.
5. Replace the old same-release transaction with a distinct-release
   transaction. It may atomically select only the two actual ABL entry files,
   `/boot/Image` and `/boot/boot/Image`; both versioned Images and both module
   trees remain side by side. A durable intent and exact preimages must exist
   before the first rename.
6. Still with the user physically present, select the candidate aliases and
   enter fastboot without first trying a normal candidate boot. RAM-boot the
   locked baseline. Independent recovery is proven only if the returned system
   simultaneously shows the baseline release/notes/Build ID while the two
   on-disk active aliases still hash as the Q6APM candidate. Seeing only a
   baseline `uname` is insufficient.
7. In that same RAM boot, atomically restore the two aliases to the baseline,
   then perform a normal reboot. Require a new boot token plus baseline
   release/notes/Build ID and baseline alias hashes. Only after the aggregate
   evaluator passes may `rollback_artifact_prevalidated` become true.

Any identity, hash, module, mount, device-count, unlock, boot-token, uptime or
attestation mismatch stops the sequence before the next state change. Only
after this recovery gate passes may supervised Q6APM candidate boot rounds
begin.
