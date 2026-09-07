# PDS-022 Panel performance routing

## Root cause

The three Panel performance buttons called `pocketds-panel-root power`, which
wrote only each CPU policy's governor. TuneD remained on its previous
`pocketds-*` profile, so GPU limits, CPU frequency caps, boost and the fan
profile could disagree with the selected label. Status inferred the label from
policy0's governor, which cannot distinguish balanced from power-saving because
both intentionally use `schedutil`.

## Single authority

The validated root helper now maps the public `powersave`, `balanced` and
`performance` values to the exact `pocketds-powersave`, `pocketds-balanced` and
`pocketds-performance` TuneD profiles, then executes the official
`/usr/sbin/tuned-adm profile` command. Extra arguments are rejected.

Panel status reads the world-readable `/etc/tuned/active_profile` state written
by TuneD. Unknown or third-party profiles are reported as `unknown` rather than
fabricating a label from one CPU governor.

The repository PPD map points to the same three profiles. Each profile owns CPU
governor/frequency limits, GPU devfreq limits/governor and its fan-profile
script, so a successful switch has one coherent authority.

## Verification boundary

Static and input-rejection tests prove routing and command boundaries without
changing live performance state. Live acceptance must switch all three modes,
run `tuned-adm verify`, read CPU/GPU/fan state, and return to the original
profile. That state-changing matrix is deferred until the current 165/60 GPU
telemetry soak completes.

## Exact 100-switch gate

The live harness now retains its original one-pass `--apply` mode and adds one
strict stress shape only: `--apply --stress-100 --confirm POCKETDS-POWER-100`
plus `POCKETDS_ALLOW_POWER_STRESS=YES`. It deterministically cycles
powersave/balanced/performance 100 times. Every switch waits for the TuneD and
fan files, runs `tuned-adm verify`, checks CPU governor/boost/all three policy
limits, GPU governor/limit and the Panel label. The original managed profile is
restored by the EXIT/INT/TERM trap.

The synthetic sysfs test completes all 100 switches and checks the exact action
sequence. It also injects a failure on action five and verifies a non-zero result
plus restoration to the original balanced profile. No service, sysfs, fan or
performance state on the Pocket DS was touched while PDS-008 was soaking. The
real 100-switch run, cold-boot persistence and post-update persistence remain
**NOT RUN**.

Implementation commit `dd0b9da0283da111d4e3230636566f1199abb881`
was transferred as device commit
`67fd1ebb4e1e6d3da760c0b37dbea0e2f3f932a9`; both resolve to tree
`f66200aabc8be2feb887a7a7936da4abe7e9dbc1`. The focused mock, lint,
`git diff --check` and full `make test` passed on both hosts, including device
live-service health. Only the synthetic root was switched 100 times; the device
TuneD/CPU/GPU/fan state was not changed. Afterwards the PDS-008 soak retained
invocation `e6d19f0a75fe4c54ad46c8eb2d9a7faa`, PID `162791`, zero restarts and its
2026-08-28 03:59:35 JST start, with no UI transaction present.
