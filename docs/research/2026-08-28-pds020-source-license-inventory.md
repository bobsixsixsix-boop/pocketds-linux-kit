# PDS-020 tracked source and SPDX inventory

Date: 2026-08-28. This tool closes an evidence gap, not a legal decision.
The repository still has no chosen root project license and no completed
third-party/source/asset redistribution record.

`pds020-source-license-inventory.py` asks Git only for `rev-parse HEAD`,
`status --porcelain` and `ls-files -z`. It then opens every tracked file once
with `O_NOFOLLOW`, requires the current user, one regular link and a 4 MiB
per-file/64 MiB aggregate bound, and records:

- repository-relative path, SHA-256, byte count and mode;
- whether bytes are strict UTF-8 text or binary;
- an SPDX license expression only when an actual
  `SPDX-License-Identifier:` header exists in the first 16 KiB;
- whether root `LICENSE`, `NOTICE`, `THIRD-PARTY.json`, `SBOM.spdx.json` and
  authoritative `components/assets/ASSETS.json` are tracked.

The report intentionally keeps `legal_conclusion=NOT_DETERMINED`,
`rights_or_redistribution_inferred=false` and `release_ready=false` regardless
of header coverage. An SPDX tag is not proof of authorship or permission, and a
missing per-file tag does not by itself choose the root license. The tool has no
network, checkout, add, commit, clean or repair path. A persistent report is a
new 0600 file and is never overwritten.

```sh
make inventory-source-licenses
OUTPUT=/path/to/new-private-inventory.json make inventory-source-licenses
```

Six fixture/static tests cover text/binary and compound SPDX detection,
missing evidence files, the authoritative nested asset-manifest path,
symlink/hardlink/oversize rejection, report no-clobber and the
no-inference/no-Git-write contract. Real clean-tree counts are recorded only
after the source and tests themselves are committed, so untracked work cannot
silently disappear from the inventory.

## Clean-tree evidence

The first committed inventory was run against local revision
`7c2e6c610e4499622f793f74e496bb86b8a60ff8` and device revision
`6f82d98c031c210ba5a814c6230f1b3d9460109d`. The commits have the same Git tree
`b8861166de31ba0a5dedd6dbacd4f015e2a619b7`; both worktrees reported zero dirty
changes. Three non-live files in the device repository checkout initially had
0600 permissions despite a non-executable 100644 Git mode. They were normalized
to 0644 without changing their bytes or any installed copy, after which the two
complete `files` arrays had the same SHA-256
`a842908724c8221a3f2c24a858bac831b4ce9018283ee0dfb20c2a231647bf5e`.

That snapshot contains 258 files and 5,586,478 bytes: 256 strict UTF-8 text
files and two binary files. Four files have an objective SPDX header:
`GPL-2.0-or-later` twice and `GPL-3.0-or-later` twice. The other 254 do not.
None of `LICENSE`, `NOTICE`, `THIRD-PARTY.json`, `SBOM.spdx.json` or the
authoritative `components/assets/ASSETS.json` is tracked. The measured report
therefore remains `legal_conclusion=NOT_DETERMINED` and `release_ready=false`;
PDS-020 is still blocked on explicit human licensing and redistribution
decisions.

That first report used schema v1 and mislabeled the asset evidence key as a
root `ASSETS.json`. Schema v2 removes the ambiguous
`root_legal_files_tracked` field and reports the exact authoritative paths in
`release_evidence_files_tracked`; the historical file/hash counts above remain
facts about the measured tree, not current release evidence.

The first clean schema-v2 comparison used local revision
`6ad030e4de67889c56b06a72fa4e443c89e80711` and device revision
`e8c64866f9df8ab48cca96dd38936886a1244ec2`.
Both were clean and had tree `80313daa3e89b2771c63595b5b53b88704616a72`.
Their complete canonical `files` arrays both hashed to
`28cd37c2373c4c6b0f9c9d5634217e84752d0ed60bd62ab51dce38ae1be70c9a`.
That tree contains 374 files and 7,240,185 bytes: 372 strict UTF-8 files and two
binary files. Five files carry objective SPDX headers (`GPL-2.0-or-later`
three times and `GPL-3.0-or-later` twice); all five release-evidence paths are
still absent, so the report remains non-authorizing and not release-ready.
