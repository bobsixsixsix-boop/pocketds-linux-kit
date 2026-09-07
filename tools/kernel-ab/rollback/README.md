# PDS-002 live baseline rollback preflight

The rollback artifact is the exact currently booted baseline composition, not
the unmodified boot image from the signed RPM. The distinction is mandatory:
the running Pocket DS boot image appends the source-verified native-lid DTB,
while the package boot image appends the upstream package DTB.

The locked live rollback image is:

```text
artifact  pds002-live-baseline-rollback-v2
size      17090560
sha256    e4e0a38ab2f247dc67b6b4521f2fcc57123528ea50edda46ea08d305d9336c94
```

`preflight.py` binds that image to five separate evidence layers:

- the runtime collector proves all three live boot aliases are identical and
  use the current raw Image plus the native-lid DTB;
- signed source evidence verifies the COPR RPM signatures, exact source tree,
  native-lid source patch, and byte-identical rebuilt custom DTB;
- independent baseline build evidence binds the signed and rebuilt RPMs to the
  same 499-entry payload and raw Image;
- the separately reproduced IFPC candidate report remains explicitly
  unauthorized for installation;
- the preflight itself parses Android bootimg v0, checks its SHA-1 image ID,
  gunzips the kernel stream to the reproduced raw Image, and verifies the
  appended custom DTB byte-for-byte.

Run only with the locked private artifacts and fresh baseline RPM extractions:

```sh
ROLLBACK_ARTIFACT_DIR=/private/pds002/live-rollback-v2 \
BASELINE_ARTIFACT_DIR=/private/pds002/baseline-artifacts \
REFERENCE_ROOT=/private/pds002/reference-root \
REBUILT_ROOT=/private/pds002/rebuilt-root \
make verify-kernel-rollback-preflight
```

This is deliberately a static preflight. The fixed lock says `not_run`, zero
successful boots, zero recovery tests, and no independently tested recovery
path. A successful report therefore keeps both
`rollback_artifact_prevalidated=false` and
`candidate_install_authorized=false`. The tool has no output, install, flash,
reboot, bootloader, or device option.
