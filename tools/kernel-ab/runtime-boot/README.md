# PDS-002 IFPC native-lid runtime boot composition

The IFPC candidate RPM cannot be used directly as a Pocket DS runtime image.
Its Android boot image appends the package DTB, while the running device uses
the separately source-verified native-lid DTB. Installing the package image
would therefore change two variables at once.

`compose.py` is an offline, fail-closed composition and verification tool. It
hashes the five fixed private inputs, binds the independently reproduced IFPC
candidate and live rollback reports, parses the candidate Android bootimg v0,
and preserves its exact gzip kernel stream. It then appends the locked
native-lid DTB and recomputes only the kernel size and legacy SHA-1 image ID.

The locked output is:

```text
artifact  pds002-ifpc-native-lid-runtime-v1
filename  ifpc-native-lid-runtime-boot-v1.img
size      17090560
sha256    b29b7c34da3b5406c60cdb7f9cd878de68ce0622de86cb6fab0e314d73c798a1
```

Create a new 0600 file in a private, non-linked directory:

```sh
ARTIFACT_DIR=/private/pds002/runtime-inputs \
OUTPUT=/private/pds002/new/ifpc-native-lid-runtime-boot-v1.img \
make compose-kernel-runtime-boot
```

Verify an existing image without modifying it:

```sh
ARTIFACT_DIR=/private/pds002/runtime-inputs \
IMAGE=/private/pds002/existing/ifpc-native-lid-runtime-boot-v1.img \
make verify-kernel-runtime-boot
```

There is no lock override, device, flash, bootloader, install, or reboot option.
Create refuses an existing path and both modes reject linked or group/world
writable inputs and output parents. Passing composition proves only the runtime
bytes: `rollback_artifact_prevalidated=false` and
`candidate_install_authorized=false` remain mandatory until supervised recovery
acceptance is complete.
