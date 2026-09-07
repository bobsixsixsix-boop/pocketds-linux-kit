# PDS-002 kernel A/B manifest planner

`planner.py` validates a pinned JSON manifest and prints a deterministic,
declarative control/candidate plan to stdout. It is intentionally **not** a
builder or installer: it never invokes Git, emits executable commands, writes
an output file, builds a kernel, installs an artifact, or contacts a device.

Safety properties:

- full 40-hex source/patch commits only;
- baseline, single experiment variable, exact ordered revert list, symmetric
  patches, and acceptance profile must match `policy.json`;
- symmetric patches appear in the same order in both variants before the
  candidate-only revert;
- unknown/duplicate keys, unpinned patches, composite variables, weak runtime,
  and incomplete controls are refused before stdout is written;
- rollback metadata is a strict schema-v2 attestation bound to the pinned
  Pocket DS device/release, Android bootimg-v0 content hash and size, validation
  report hash, at least one successful boot and recovery test, and an explicitly
  tested independent recovery path;
- output includes variant/recipe/manifest/policy/plan SHA-256 identities plus
  fixed-control and rollback acceptance metadata;
- the local policy path is fixed beside the planner and cannot be replaced by a
  CLI option.

## Fixture warning

Files under `fixtures/` are test templates, **not valid real manifests**. Their
firmware, kernel-config, rollback image and rollback report fields begin with
`PLACEHOLDER-NOT-FOR-REAL-PLAN:`. The production CLI explicitly refuses those
sentinels and exits 2 with empty stdout. Unit tests replace them only in memory
with deterministic synthetic hashes before exercising successful paths.

A real manifest must contain hashes measured from the exact authoritative
firmware set and kernel config, plus a rollback artifact already validated
outside this tool. The planner validates the attestation structure; the future
builder must still rehash the referenced image, verify the validation report
and match both to the manifest before any installation. A string such as
`pending` can no longer satisfy the rollback gate. Both current P0 experiments
require at least three matched runs.

## Matched IFPC evaluation

`ab-evaluate.py` is the offline acceptance boundary for the first IFPC
experiment. It neither starts a workload nor changes a display, boot alias,
kernel, service or power state. The supervised collector runs happen
separately and their JSONL stdout must be captured into private mode-0600
files.

The matrix is deliberately stronger than a single good candidate boot:

- three profiles: dual 165/60, dual 60/60 and upper-only 165;
- three rounds for both baseline and candidate, making 18 reports and nine
  matched pairs;
- one boot per variant/round across all three profiles, and six distinct boots
  overall;
- exact `/sys/kernel/notes` identity for the baseline or candidate despite
  their identical release strings;
- exact KWin binary, A740 SQE firmware, GMU firmware, smooth fan controller,
  repository revision and per-profile workload SHA-256;
- `pocketds-performance` TuneD plus the `aggressive` fan profile in the header,
  every five-second sample and the final summary, with all fixed controls
  rehashed at the end;
- at least 45 minutes, 95% sample/GPU-telemetry coverage, bounded gaps,
  unbroken journal/display/boot state and collector CPU at or below 0.5%;
- zero candidate GMU/HFI/lockup/recovery/fence events or critical interactive
  D-state, with EBUSY, SMMU faults, PSI and collector CPU non-regression;
- at least 95% fresh GPU-temperature coverage, an 85°C absolute ceiling and no
  more than a 3°C candidate increase over the paired baseline maximum.

The profile locks make the thermal comparison reproducible; they do not prove
acoustic quality or replace the separate PWM/target-PWM/RPM evidence and
supervised fan observation.

The private ledger names reports by basename only and records the four
supervised rollback observations for every run. Report files may not be
symlinked, hardlinked, public, duplicated or reused across cells. The aggregate
never records raw boot IDs or input paths and always leaves
`candidate_install_authorized=false`; even a passing A/B result does not bypass
the independent recovery and human change-control boundary.

Each formal collector invocation must pass the SHA-256 of the frozen workload
definition:

```sh
./scripts/pds002-gpu-kwin-baseline.py \
  --duration 2700 --interval 5 \
  --workload-sha256 "$WORKLOAD_SHA256" \
  --repo-revision "$(git rev-parse HEAD)"
```

The current frozen definition is `workload/manifest.json`: a local-only WebGL2
fragment workload plus 48 animated compositor layers. It binds the exact HTML,
the installed signed Chromium V4L2 wrapper/runtime, all launch flags and 2700
seconds of execution. `make preflight-kernel-ab-workload` only rehashes those
files and prints the manifest SHA-256; it does not open Chromium or touch the
desktop. The same workload identity is used for all three display profiles.

`workload/run.py` is plan-only by default. A formal cell needs the exact
confirmation, declared variant/profile/round and the exact new filename from
the ledger. `make preflight-kernel-ab-run` performs the same workload,
repository, session, kernel, display, controller, profile and zero-browser
checks without reserving a report or starting either process. Before reserving
the private mode-0600 report, formal execution revalidates every one of those
gates.
It then owns one temporary Chromium profile and one collector process group,
preserves an interrupted JSONL report on failure, and cleans up only those
owned processes/files. It refuses a mismatched TuneD or fan profile instead of
changing either one. It has no display-setting, boot, service, package,
network-fetch or power path.

Once all reports and the ledger are present in one private directory:

```sh
LEDGER=/private/pds002/ifpc/ledger.json \
OUTPUT=/private/pds002/ifpc/evaluation.json \
REQUIRE_PASS=1 make evaluate-kernel-ab
```

`make prepare-kernel-ab-ledger` creates the 18 deterministic report names in a
new mode-0600 ledger. All four operator observations are deliberately `null`;
the evaluator refuses the template until the supervised result for every run
has been explicitly filled with booleans. The generator never guesses a pass.

## Read-only runtime evidence

`evidence.py` hashes the exact pinned running raw kernel, its module-tree and
boot config copies, all three Android bootimg v0 paths, the appended/versioned
DTB, and the A740 firmware files declared by `evidence-spec.json`. The fixed
Pocket DS baseline contains only
`qcom/a740_sqe.fw` and `qcom/gmu_gen70200.bin`: the pinned kernel catalog names
those files, the current boot log confirms both new-location loads, and the
current device tree exposes no `zap-shader` node or firmware-name override.

Run it on the matching device without privilege:

```sh
make observe-kernel-evidence
```

The collector is stdout-only, follows no symlinks, accepts no alternate root or
output path, performs no subprocess/network/write operation, and refuses a
release, machine identity, config mirror, boot alias, raw-kernel/gzip, FDT
structure/size, DTB mirror, missing firmware, or unsafe-file mismatch. Its two
`planner_controls` values are measured facts. It deliberately leaves
`production_plan_ready=false`: an RPM release match does not cryptographically
bind the running image to the claimed Git commit, and a prevalidated rollback
artifact still does not exist.

## Offline package and source evidence

`source-evidence.py` checks the pinned COPR metadata, binary/source RPMs, COPR
key, Sequoia verifier, SRPM source member, exact GitLab commit archive, native
lid source patch, and independently rebuilt lid DTB. It normalizes 99,850
source entries and requires the SRPM tree to equal commit `a4975ce7...`. It
also parses RPM tag 268 itself and verifies both signed main headers with the
pinned COPR fingerprint. The tool has no network/install path; transient
signature material is created with mode 0600 below a private temporary
directory and removed automatically.

Run it only with previously downloaded, content-addressed artifacts:

```sh
REPOMD=/path/repomd.xml \
PRIMARY=/path/primary.xml.gz \
BINARY_RPM=/path/kernel.aarch64.rpm \
SOURCE_RPM=/path/kernel.src.rpm \
SRPM_SOURCE_ARCHIVE=/path/linux-7.1-rc2.tar.gz \
COMMIT_ARCHIVE=/path/linux-a4975ce7.tar.gz \
COPR_KEYRING=/path/pubkey.gpg \
SQV=/path/sqv \
REBUILT_DTB=/path/qcs8550-ayaneo-pocketds-native-lid.dtb \
make verify-kernel-source-evidence
```

`source-evidence.py` remains intentionally scoped to package/source
attribution, so its standalone report still says
`source_to_binary_provenance_complete=false`.  The separate
`reproducible-build/payload-evidence.py` replays the now-completed independent
build attestation: all 496 RPM-declared payload entries match, and both
extracted trees have the same 499 entries including 341 regular files. Keeping
the collectors separate prevents a source archive check from silently
inheriting a build claim when the rebuild artifacts are absent.

The reproduction evidence is offline and read-only:

```sh
python3 tools/kernel-ab/reproducible-build/payload-evidence.py \
  --artifact-dir /private/pds002/artifacts \
  --reference-root /private/pds002/reference \
  --rebuilt-root /private/pds002/rebuilt \
  --pretty
```

Its fixed lock distinguishes complete payload identity from RPM-container
identity.  It uses fixed `/usr/bin/rpm` header queries to bind each RPM to its
own extraction before comparing the normalized trees.  The payload identity is
proven; container identity is false because the rebuild is unsigned and has
different RPM header/signature bytes.  Neither result provides a prevalidated
rollback image.

Example refusal:

```sh
python3 tools/kernel-ab/planner.py tools/kernel-ab/fixtures/ifpc-only.json
```

The generated `assert-source`, `apply-symmetric`, and `revert-variable` records
are declarative patch order. A separately reviewed future builder would still
need to prove a clean exact source tree, commit ancestry, patch applicability,
rollback readiness and the two rollback hashes. No such builder or install
target is included here. `patches/0002-revert-a740-ifpc.patch` is only the
reviewable single-variable source delta; its presence does not make an install
plan valid.
