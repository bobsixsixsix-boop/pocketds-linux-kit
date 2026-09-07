# NetworkManager wait-online policy

Date: 2026-08-27 (Asia/Tokyo)

Issue: PDS-016.

## Evidence

The repository installed an override that replaced `nm-online -s -q` with a
five-second timeout. Across the current and five previous boots the unit failed
after 5.01-5.05 seconds every time. On the current boot NetworkManager reported
`startup complete` much later than that timeout, so the override guaranteed a
red failed unit rather than accelerating device or Wi-Fi initialization.

The only reverse consumer of `network-online.target` was
`dnf-makecache.timer`. It first fires roughly ten minutes after boot and its
network operation has its own ordinary failure/retry behavior. No repository
service declares `network-online.target`, and interactive applications already
handle connectivity changes after login.

## Decision

Delete the timeout override, restore Fedora's vendor `nm-online -s -q` command
and 60-second timeout, and clear the stale failed state. Recent boots reach
startup-complete in roughly 16-22 seconds, so the vendor unit can finish
successfully in the background without delaying `graphical.target`. Do not
replace it with `/bin/true`, because that would falsely claim network-online
semantics; do not disable it while a system package still explicitly wants
`network-online.target`.

The installer backs up the former drop-in before removing it. If a future
boot-critical service genuinely requires different online timing, review that
service and the relevant connection's `may-fail`, carrier, and autoconnect
properties rather than adding another arbitrary timeout.

## Verification

Current-session verification removes the drop-in, resets the old failure, and
starts the vendor unit after NetworkManager is already settled. PDS-016 remains
VERIFICATION until a cold boot shows a successful background completion, no
graphical-path regression, and normal Wi-Fi plus package metadata refresh.
