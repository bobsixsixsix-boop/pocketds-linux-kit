# PDS-005/PDS-011 UI deployment drift audit

Date: 2026-08-28. This is a read-only pre-deployment inventory while the formal
24-hour telemetry soak is running. No file was installed, copied or removed;
Plasma, KWin and the keyboard service were not restarted.

Direct SHA-256 comparison established that the installed Panel QML, Panel root
helper and keyboard main program differ from the authoritative repository. The
installed bytes can be reproduced exactly from these historical commits:

| Installed artifact | Historical source commit |
|---|---|
| Panel `main.qml` | `15041392a1a915426d34afeec26804711c23bcb5` |
| Panel root helper | `af922073e274705b6e8dd2d8116b566bede1d0b9` |
| keyboard main program | `88b00296077eef84bf1f85b9374907274f5e346d` |

Panel metadata, Plasma recovery program/unit, keyboard visibility state and
keyboard unit already match. The keyboard adapter bytes match but its installed
mode is 0755 rather than the declared 0644, so the strict audit correctly calls
it `UNSAFE`. The newer keyboard geometry and voice artifact modules are not
installed. The compiled `pocketds-panelctl` cannot be proven from a source-text
hash and was deliberately not rebuilt during the soak.

`pds005-ui-deployment-audit.py` now makes this boundary repeatable. It compares
11 direct-copy artifacts using a single `O_NOFOLLOW` descriptor and requires a
regular, single-link, bounded file with the exact expected owner and live mode.
Its report contains only logical artifact IDs and SHA-256 values, never absolute
paths, username or hostname. States remain distinct: `MATCH`, `DRIFT`,
`MISSING`, `UNSAFE`, `SOURCE_UNSAFE` and `NOT_EVALUATED`.

The compiled Panel reader is a separate twelfth record. Without
`--panelctl-candidate` it is always `NOT_EVALUATED`, making the overall result
incomplete. With an explicit mode-0755 current-user candidate, the audit only
compares that candidate with the root-owned installed binary and labels the
binding as caller supplied; it never compiles or installs anything itself.

```sh
make audit-ui-deployment
PANELCTL_CANDIDATE=/path/to/current-build/pocketds-panelctl \
  make audit-ui-deployment-candidate
```

Six fixture/static tests cover exact matches, drift, missing files, mode and
link rejection, privacy, candidate omission and 0600 no-overwrite reports.
Deployment remains deferred until the soak completes. The post-deployment gate
is all twelve artifacts `MATCH`, followed by service PID/restart checks and the
already planned physical screenshot/touch acceptance.

Source commits are local `35dc1aa` and device `13de8c5`. Six targeted tests and
full `make test` passed on both hosts. The live read-only audit exited 1 as
designed with MATCH 5, DRIFT 3, MISSING 2, UNSAFE 1 and NOT_EVALUATED 1; no
candidate binary was supplied. The repository remained clean and the telemetry
soak remained PID 162791 with zero restarts. No deployment or service restart
occurred.

A follow-up compiled `pocketds-panelctl.cpp` with the exact project command
(`c++ -std=c++17 -O2 -Wall -Wextra`) into a unique temporary directory. The
candidate and installed binary both hashed to
`ea601505a14d28dd624ef50e2828d1ec00ad9d4ece1744dd43ab50b940499d1d`, so the
binary is already current and must not be needlessly replaced. The temporary
directory was removed on exit. The final pre-deployment count is therefore
MATCH 6, DRIFT 3, MISSING 2 and UNSAFE 1. The minimal later transaction is six
changes only: Panel QML, Panel root helper, keyboard main, adapter mode 0755 to
0644, and installation of geometry plus voice-artifact modules.
