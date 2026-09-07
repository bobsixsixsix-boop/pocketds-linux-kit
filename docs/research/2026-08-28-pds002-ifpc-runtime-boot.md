# PDS-002 IFPC native-lid runtime boot — 2026-08-28

## Why the candidate RPM boot image is not installable

The candidate RPM keeps the package DTB (`0557cee1...`). The current Pocket DS
boot composition instead uses the separately source-verified native-lid DTB
(`195345de...`). Using the RPM boot image directly would change both IFPC and
the device tree, invalidating the intended single-variable A/B test.

## Deterministic composition

The runtime composition preserves the candidate gzip stream byte-for-byte:

```text
candidate raw Image  ccfe5f24d9710d624bc99b4bcbe1dafde06914795a1802645b02b55f43bfdcba
candidate gzip       b0c484c93dfe20d2967718596c9370901ef7f62220ec5f041f8432e47b7f5bae
native-lid DTB       195345de379195df466b999cf7c7ba53764dfd9c0bb12b18214083e530979556
runtime image        b29b7c34da3b5406c60cdb7f9cd878de68ce0622de86cb6fab0e314d73c798a1
runtime size         17090560
kernel blob size     17087382
image ID             b1ae63bbad931bc5d1e9d6de38de15cbadde935f000000000000000000000000
```

The legacy Android v0 ID was independently checked as SHA-1 over the composed
kernel blob and little-endian kernel/ramdisk/second sizes. The tool recomputes
the kernel size and ID while preserving the remaining header and exact gzip.

An earlier independently composed image was verified by the final tool. The
same tool then created a new 0600 image in a separate private directory. Both
images have the exact runtime SHA-256 above. The retained pretty reports are:

```text
verify report  dcf604b7c52ae6506e5f71b002b1e124504619e57568499e978baf23420868f4
create report  6bf32c88bc731899ce32b2c41ea7fe7db9acfd4ae1b95cc07d94edfdee9115c3
```

## Safety boundary

The repository lock fixes all five inputs and the output. Create uses exclusive
0600 creation and refuses existing paths, links, group/world-writable parents,
input drift, package-DTB drift and reports that overclaim readiness. There is no
device, install, flash, bootloader or reboot option.

Composition is not recovery validation. Both reports deliberately retain:

```text
rollback_artifact_prevalidated=false
candidate_install_authorized=false
```

No boot image was copied to or written on the Pocket DS.
