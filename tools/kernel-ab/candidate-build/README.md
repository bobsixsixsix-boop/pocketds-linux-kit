# PDS-002 A740 IFPC candidate evidence

This directory pins and verifies the first PDS-002 kernel candidate. The only
intended source change is the exact revert in
`../patches/0002-revert-a740-ifpc.patch`; `ifpc-kernel-spec.patch` adds that
single patch to the otherwise unchanged source RPM recipe.

The first isolated Fedora 44/aarch64 build produced a candidate with the same
499 extracted payload paths and the same 341 regular-file sizes as the signed
baseline. Exactly two files differ: the raw kernel `Image` and the Android
bootimg derived from it. All 323 modules, both DTBs, `System.map`, config, and
module metadata are byte-identical.

`ifpc-evidence.py` is an offline, read-only verifier. It hashes every locked
artifact and recipe, queries both source and binary RPM manifests through the
same locked read-only file descriptors, binds each binary RPM to its extraction,
and proves all of the following:

- the candidate source RPM adds only the revert and modifies only `kernel.spec`;
- the spec change is exactly the `Patch0` declaration and fail-closed `%prep`
  application;
- the compiler, installed-package set, wrappers, and complete build log match
  the lock;
- the extracted path set is unchanged and only raw/boot Image records differ;
- all 29 raw Image byte differences are confined to the A740 IFPC quirk,
  removed IFPC reglist pointer, build ID, and packed RELR consequence;
- each bootimg contains the locked gzip stream followed by the same locked DTB,
  gunzips to its corresponding raw Image, and changes no header field except
  kernel size and image ID.

Run against already prepared, private artifacts and fresh RPM extractions:

```sh
BASELINE_ARTIFACT_DIR=/private/pds002/baseline-artifacts \
CANDIDATE_ARTIFACT_DIR=/private/pds002/ifpc-candidate-artifacts \
REPRODUCED_CANDIDATE_ARTIFACT_DIR=/private/pds002/ifpc-reproduced-artifacts \
BASELINE_ROOT=/private/pds002/baseline-root \
CANDIDATE_ROOT=/private/pds002/ifpc-candidate-root \
REPRODUCED_CANDIDATE_ROOT=/private/pds002/ifpc-reproduced-root \
make verify-kernel-ifpc-candidate
```

There is deliberately no lock override, output, build, download, install, or
flash option. The locked second build uses a distinct RPM container but has an
identical RPM header manifest and 499-entry extracted payload, so a successful
report sets `candidate_independently_reproduced=true`. It still sets
`rollback_artifact_prevalidated=false` and
`candidate_install_authorized=false`. It is evidence about the candidate, not
permission to put it on a Pocket DS.

The candidate RPM's boot image contains the package DTB. The live device uses a
separately source-verified native-lid DTB, so the RPM boot image is not itself a
valid runtime A/B artifact. The separate `../runtime-boot/compose.py` step now
combines the candidate gzip/raw Image with that exact custom DTB, recomputes the
Android v0 size and ID, and proves the locked result byte-for-byte. That closes
only the composition gap; recovery validation and installation remain separate,
closed gates.
