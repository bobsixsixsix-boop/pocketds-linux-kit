# PDS-002 live rollback static preflight — 2026-08-28

## Correct rollback identity

The signed RPM boot image is not the live Pocket DS boot composition. The RPM
image appends the package DTB with SHA-256 `0557cee1...`; the three current live
aliases append the source-verified native-lid DTB with SHA-256 `195345de...`.
Treating the package image as the tested rollback would have silently changed a
second variable.

The corrected rollback artifact is an exact off-device copy of the currently
booted live image:

```text
artifact  pds002-live-baseline-rollback-v2
sha256    e4e0a38ab2f247dc67b6b4521f2fcc57123528ea50edda46ea08d305d9336c94
size      17090560
```

All three live aliases had that exact identity at collection. The image uses an
unchanged 16,951,563-byte gzip stream with SHA-256 `1549b173...`; it expands to
the independently reproduced baseline raw Image `b77e3cec...`. It appends the
135,820-byte native-lid DTB `195345de...`, and its Android v0 image ID is
`da1d98ea3f9109c9319a7071d5850845d5df0535`.

## Bound evidence

The static preflight binds:

- current runtime boot aliases and boot composition;
- signed COPR binary/SRPM provenance and the exact 99,850-entry source tree;
- native-lid source patch and byte-identical DTB rebuild;
- independently reproduced 499-entry baseline payload;
- independently reproduced IFPC-only candidate evidence, still unauthorized.

The retained live v2 static report has SHA-256
`fcec19447429f2b551afc67307860887fec37a61659aa97a73be63b06bee927e`.
The earlier package-image-only experiment is superseded and must not be used.

## Boundary

Static composition evidence is not a recovery test. The lock fixes these facts:

```text
status=not_run
successful_boots=0
successful_recovery_tests=0
independent_recovery_path=false
rollback_artifact_prevalidated=false
candidate_install_authorized=false
```

The candidate runtime boot has since been deterministically composed with the
same native-lid DTB and recorded separately. The next work is to design and
supervise a truly independent recovery path. No boot image has been written to
the Pocket DS.
