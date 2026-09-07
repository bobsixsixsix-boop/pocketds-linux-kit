# PDS-002 A740 IFPC candidate reproduction — 2026-08-28

## Outcome

The A740 IFPC candidate is built and independently reproduced, but is not
authorized for installation. Both builds used the exact candidate source RPM
and separate clones of the retained, locked Fedora 44/aarch64 baseline Mock
root. The pre-build package manifests contain 326 sorted NEVRA records and have
the same SHA-256:

```text
66e7c67c43ad740150ba80038789f384348ce869fc24d6d08691a1952fdb881b
```

The candidate source RPM adds exactly
`0002-revert-a740-ifpc.patch` and changes only `kernel.spec` to declare and
apply it with `--batch --fuzz=0 --reject-file=-`. The original source tar,
initramfs archive, and config are byte-identical to the signed baseline SRPM.

## Independent build identities

The two unsigned binary RPM containers are intentionally recorded separately:

```text
first       070fea36c7cc293b43133d0ae6914b7747cb2a938b623dc5097c5c7824ca5e7f
reproduced  beecd536e422f3a41d103b81ffb1e2ee6df697b8d304806cf3e1fd59be765af9
size        37130797 bytes each
```

Their RPM header payload manifests have the same SHA-256
`5874e5b024ffb003ca1e57df60e75b1bf3e71fb62bdf79ff5a7646322da68940`.
Fresh extractions produce identical 499-record normalized trees:

```text
manifest       73593580e8b5bd748c8b897b4ef9b9c6cfa26ea24dc16e307fe708a37b4e421d
entries        499
regular files  341
directories    156
symlinks       2
regular bytes  81183506
```

## Complete baseline-to-candidate delta

The baseline and candidate have the same path set. Exactly two regular files
differ: the raw kernel `Image` and the Android bootimg derived from that raw
Image. All other 497 entries, including 339 other regular files and all 323
modules, are identical. Both Pocket DS DTBs, `System.map`, config, and module
metadata are unchanged.

The raw Image has exactly 29 changed bytes:

- one byte in the A740 quirk bit field (`0xb8` to `0x38`);
- seven differing bytes in the removed eight-byte IFPC reglist pointer;
- twenty bytes in the derived GNU build ID;
- one byte in packed RELR data derived from the removed pointer.

No raw Image byte outside those locked slices differs. The two boot images
gunzip exactly to their respective raw Images, append the same 135,527-byte DTB
with SHA-256 `0557cee1...`, and differ in no header field except kernel size and
the derived image ID.

## Evidence boundary

`tools/kernel-ab/candidate-build/ifpc-evidence.py` replays this evidence
offline and read-only. The retained v3 report has SHA-256:

```text
d84ea7af4e237ed89c68b0b5062f4ac7175eab5363af9bd463d8d329f3c5f87e
```

Its gates are intentionally split:

```text
candidate_built=true
source_inputs_except_revert_identical=true
single_variable_payload_delta_proven=true
candidate_independently_reproduced=true
rollback_artifact_prevalidated=false
candidate_install_authorized=false
```

No candidate kernel, module, boot image, or RPM was installed on the Pocket DS.
The next mandatory gate is a separately hashed baseline rollback bootimg and
validation report, followed by a real successful boot, a real recovery test,
and an independent recovery path. Only after those facts exist may the
schema-v2 planner authorize supervised matched A/B runs.
