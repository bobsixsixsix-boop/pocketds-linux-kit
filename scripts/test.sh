#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)

echo '[test] static validation'
"$repo_root/scripts/lint.sh"

echo '[test] offline SD image staging, first use and expansion boundaries'
python3 "$repo_root/tests/sd-image-stage.py"
python3 -m unittest discover -s "$repo_root/tests" -p 'test_sd_image_*.py'

echo '[test] bounded Simplified Chinese and Rime Ice installer'
python3 "$repo_root/tests/chinese-input-install-policy.py"
python3 "$repo_root/tests/chinese-catalog-restore.py"

echo '[test] installed-app desktop shortcuts'
python3 "$repo_root/tests/desktop-shortcuts-policy.py"
python3 "$repo_root/tests/android-desktop-entry.py"
python3 "$repo_root/tests/current-kernel-trial.py"
python3 "$repo_root/tests/dsc-v4-install.py"
python3 "$repo_root/tests/daily-suspend.py"
python3 "$repo_root/tests/daily-suspend-install-transaction.py"
python3 "$repo_root/tests/attended-dsc-lid-test.py"
python3 "$repo_root/tests/attended-stable-layout-test.py"
python3 "$repo_root/tests/attended-lid-cycle.py"
python3 "$repo_root/tests/attended-native-lid-cycle.py"

echo '[test] lid light-standby state machine'
python3 "$repo_root/tests/light-standby-state-machine.py"
python3 "$repo_root/tests/lid-mode.py"
python3 "$repo_root/tests/panel-lid-actions.py"
python3 "$repo_root/tests/gamepad-activity.py"

echo '[test] conditional lid switch bridge'
python3 "$repo_root/tests/lid-switch-bridge.py"
python3 "$repo_root/tests/libinput-lid.py"

echo '[test] privileged helper input validation'
"$repo_root/tests/panel-root-invalid-inputs.sh"

echo '[test] verified ABL BootMode transaction'
python3 "$repo_root/tests/boot-mode-switch.py"
python3 "$repo_root/tests/boot-switch-result.py"
python3 "$repo_root/tests/android-boot-switch-source.py"
python3 "$repo_root/tests/shared-game-library-stage.py"

echo '[test] installer root trust ordering'
python3 "$repo_root/tests/install-privilege-order.py"
python3 "$repo_root/tests/install-service-upgrade.py"

echo '[test] panel status schema'
python3 "$repo_root/tests/panel-status-schema.py"

echo '[test] panel battery fixture'
python3 "$repo_root/tests/panel-battery-fixture.py"
python3 "$repo_root/tests/panel-cache-isolation.py"

echo '[test] GPU observer smoke test'
python3 "$repo_root/tests/gpu-observer-smoke.py"

echo '[test] PDS-002 GPU/KWin baseline collector'
python3 "$repo_root/tests/pds002-baseline-smoke.py"

echo '[test] PDS-002 fail-closed kernel A/B planner'
python3 "$repo_root/tests/kernel-ab-planner.py"

echo '[test] PDS-002 pinned read-only runtime evidence'
python3 "$repo_root/tests/kernel-ab-evidence.py"

echo '[test] PDS-002 signed package/source snapshot evidence'
python3 "$repo_root/tests/kernel-source-evidence.py"

echo '[test] PDS-002 independent kernel payload reproduction evidence'
python3 "$repo_root/tests/kernel-payload-reproduction.py"

echo '[test] PDS-002 A740 IFPC single-variable candidate evidence'
python3 "$repo_root/tests/kernel-ifpc-candidate.py"

echo '[test] PDS-002 private matched IFPC A/B evaluator'
python3 "$repo_root/tests/kernel-ab-workload.py"
python3 "$repo_root/tests/kernel-ab-workload-run.py"
python3 "$repo_root/tests/kernel-ab-ledger.py"
python3 "$repo_root/tests/kernel-ab-evaluate.py"

echo '[test] PDS-002 live baseline rollback static preflight'
python3 "$repo_root/tests/kernel-rollback-preflight.py"

echo '[test] PDS-002 IFPC + native-lid runtime boot composition'
python3 "$repo_root/tests/kernel-runtime-boot-compose.py"

echo '[test] PDS-002 independent fastboot RAM-recovery path'
python3 "$repo_root/tests/kernel-recovery-host-preflight.py"
python3 "$repo_root/tests/kernel-recovery-runtime-attest.py"
python3 "$repo_root/tests/kernel-recovery-dispatch.py"
python3 "$repo_root/tests/kernel-recovery-evaluate.py"
python3 "$repo_root/tests/kernel-recovery-transaction.py"

echo '[test] PDS-001 gated deep-cycle acceptance harness'
python3 "$repo_root/tests/pds001-deep-cycle.py"

echo '[test] PDS-001 bounded deep-cycle report series'
python3 "$repo_root/tests/pds001-deep-series.py"

echo '[test] GPU telemetry cache'
python3 "$repo_root/tests/gpu-telemetry-smoke.py"

echo '[test] PDS-008 display telemetry and Panel contract'
python3 "$repo_root/tests/display-telemetry.py"

echo '[test] unified Gamescope runtime and limiter'
"$repo_root/tests/gamescope-runtime-mock.sh"

echo '[test] Moonlight client presentation FPS hook'
"$repo_root/tests/moonlight-fps-hook.sh"
"$repo_root/tests/moonlight-fps-hook-install.sh"

echo '[test] Steam/Proton private presentation FPS wrapper'
"$repo_root/tests/steam-game-fps-wrapper.sh"
"$repo_root/tests/steam-switchdeck-launcher-mock.sh"

echo '[test] managed Steam session and existing-client forwarding'
"$repo_root/tests/steam-session-launcher-mock.sh"

echo '[test] Steam desktop switch keeps the KDE login session'
"$repo_root/tests/steam-desktop-selector-mock.sh"

echo '[test] PDS-008 telemetry soak harness'
python3 "$repo_root/tests/pds008-telemetry-soak.py"

echo '[test] PDS-006 private supervised battery matrix evaluator'
python3 "$repo_root/tests/pds006-battery-matrix-evaluate.py"

echo '[test] PDS-017 SM8550 battery charge-unit candidate'
python3 "$repo_root/tests/pds017-battery-charge-unit.py"

echo '[test] PDS-014 gated post-boot read-only matrix'
python3 "$repo_root/tests/pds014-post-boot-acceptance.py"

echo '[test] PDS-014 five-distinct-boot private report aggregator'
python3 "$repo_root/tests/pds014-post-boot-series.py"

echo '[test] PDS-020 mounted-root release audit'
python3 "$repo_root/tests/pds020-release-root-audit.py"

echo '[test] PDS-020 tracked source/SPDX inventory'
python3 "$repo_root/tests/pds020-source-license-inventory.py"

echo '[test] clean source preview export'
python3 "$repo_root/tests/source-preview.py"

echo '[test] copied third-party source and license pins'
python3 "$repo_root/tests/third-party-source-pins.py"

echo '[test] InputPlumber mouse-clear source candidate'
python3 "$repo_root/tests/inputplumber-mouse-clear-contract.py"

echo '[test] PDS-020 pinned offline SPDX 3.0.1 validation'
python3 "$repo_root/tests/pds020-spdx-offline-validate.py"

echo '[test] PDS-020 wallpaper C2PA provenance boundary'
python3 "$repo_root/tests/pds020-asset-c2pa-verify.py"

echo '[test] PDS-020 explicit installer asset profiles'
python3 "$repo_root/tests/pds020-install-asset-preflight.py"

echo '[test] PDS-020 non-flashable composition boundary'
python3 "$repo_root/tests/pds020-composition-preflight.py"

echo '[test] PDS-020 read-only final rootfs smoke'
python3 "$repo_root/tests/pds020-rootfs-smoke.py"

echo '[test] PDS-020 pocketds-userspace signed SRPM hardening'
python3 "$repo_root/tests/pds020-userspace-srpm-prepare.py"

echo '[test] PDS-020 pocketds-userspace binary RPM payload boundary'
python3 "$repo_root/tests/pds020-userspace-rpm-audit.py"

echo '[test] PDS-020 pocketds-userspace two-result RPM reproduction gate'
python3 "$repo_root/tests/pds020-userspace-rpm-repro.py"

echo '[test] PDS-020 pocketds-userspace isolated post-sign receipt gate'
python3 "$repo_root/tests/pds020-userspace-rpm-postsign.py"

echo '[test] PDS-020 pocketds-userspace disposable mock transaction cycle'
python3 "$repo_root/tests/pds020-userspace-rpm-transaction.py"

echo '[test] PDS-020 immutable pds2 dependency RPM archive'
python3 "$repo_root/tests/pds020-userspace-rpm-archive-verify.py"

echo '[test] PDS-020 pocketds-userspace dependency-resolved pds2 transaction'
python3 "$repo_root/tests/pds020-userspace-rpm-full-transaction.py"

echo '[test] PDS-020 empty-root local pds2 package transaction'
python3 "$repo_root/tests/pds020-userspace-rpm-rootfs-transaction.py"

echo '[test] PDS-020 reproducible unsigned pds2 repository metadata'
python3 "$repo_root/tests/pds020-userspace-rpm-repository-verify.py"

echo '[test] PDS-020 future signed pds2 repository receipt chain'
python3 "$repo_root/tests/pds020-userspace-rpm-repository-postsign.py"

echo '[test] PDS-020 future DNF5 repository/package signature smoke'
python3 "$repo_root/tests/pds020-userspace-rpm-repository-smoke.py"

echo '[test] brightness state machine'
python3 "$repo_root/tests/brightness-state-machine.py"

echo '[test] PowerDevil brightness isolation'
python3 "$repo_root/tests/brightness-powerdevil.py"
python3 "$repo_root/tests/brightness-daemon.py"

echo '[test] keyboard visibility state machine'
python3 "$repo_root/tests/keyboard-visibility-state.py"

echo '[test] keyboard runtime adapter'
python3 "$repo_root/tests/keyboard-adapter.py"
python3 "$repo_root/tests/keyboard-window-transfer.py"

echo '[test] touchpad button ownership and lower-screen raw input'
python3 "$repo_root/tests/touchpad-gestures.py"
python3 "$repo_root/tests/touchpad-raw.py"
python3 "$repo_root/tests/touchpad-reader-lifecycle.py"
python3 "$repo_root/tests/touchpad-button-ownership.py"

echo '[test] keyboard lower-screen geometry'
python3 "$repo_root/tests/keyboard-geometry.py"

echo '[test] private and fresh keyboard voice artifacts'
python3 "$repo_root/tests/voice-artifacts.py"
python3 "$repo_root/tests/asr-api.py"
python3 "$repo_root/tests/asr-api-provision.py"
python3 "$repo_root/tests/asr-api-install-transaction.py"

echo '[test] locked offline SenseVoice artifact installer'
python3 "$repo_root/tests/pds011-sensevoice-prepare.py"

echo '[test] privacy-minimal Chinese ASR evaluator'
python3 "$repo_root/tests/pds011-asr-evaluate.py"

echo '[test] lower-screen UI design contract'
python3 "$repo_root/tests/ui-design-contract.py"

echo '[test] handheld desktop mouse profile'
python3 "$repo_root/tests/joymouse-profile.py"

echo '[test] temporary controller capture and hardware-action gates'
python3 "$repo_root/tests/controller-test-backend.py"
python3 "$repo_root/tests/controller-test-gate.py"
if [[ $(uname -s) == Linux ]]; then
    python3 "$repo_root/tests/controller-test-client.py"
    python3 "$repo_root/tests/controller-test-native.py"
fi

echo '[test] pinned InputPlumber haptics sidecar'
python3 "$repo_root/tests/inputplumber-haptics-contract.py"
python3 "$repo_root/tests/inputplumber-haptics-installer.py"

echo '[test] Wiliwili exact GLFW controller mapping'
python3 "$repo_root/tests/wiliwili-gamecontrollerdb.py"
python3 "$repo_root/tests/wiliwili-input-install-policy.py"
"$repo_root/tests/wiliwili-input-installer-mock.sh"
"$repo_root/tests/wiliwili-launcher-mock.sh"
python3 "$repo_root/tests/wiliwili-launcher-install-policy.py"
python3 "$repo_root/tests/game-session-lock-policy.py"

echo '[test] PDS-010 passive attended Wiliwili control harness'
python3 "$repo_root/tests/pds010-wiliwili-attended.py"

echo '[test] PDS-005 reversible Applet-33 migration'
python3 "$repo_root/tests/pds005-applet-migration.py"

echo '[test] PDS-005 fail-closed applet geometry helper'
python3 "$repo_root/tests/pds005-panel-geometry.py"

echo '[test] PDS-005 bounded Plasma recovery state machine'
python3 "$repo_root/tests/pds005-plasma-recovery.py"

echo '[test] PDS-005 touch Panel action boundary'
python3 "$repo_root/tests/pds005-panel-actions.py"

echo '[test] live Codex account quota with fail-closed fallback'
python3 "$repo_root/tests/codex-quota.py"
python3 "$repo_root/tests/codex-quota-verify.py"
python3 "$repo_root/tests/codex-quota-install-policy.py"

echo '[test] PDS-005 source-versus-installed UI audit'
python3 "$repo_root/tests/pds005-ui-deployment-audit.py"

echo '[test] PDS-005 exact staged UI update transaction'
python3 "$repo_root/tests/pds005-ui-update-transaction.py"

echo '[test] PDS-004/005/010 supervised UI/input matrix evaluator'
python3 "$repo_root/tests/pds005-ui-input-evaluate.py"

echo '[test] PDS-004 isolated nested Xwayland failure harness'
python3 "$repo_root/tests/pds004-xwayland-isolation.py"

echo '[test] PDS-005 disabled standalone Panel contract'
python3 "$repo_root/tests/pds005-standalone-contract.py"

echo '[test] audio routing configuration'
python3 "$repo_root/tests/audio-routing-config.py"

echo '[test] cold-boot audio readiness barrier'
python3 "$repo_root/tests/audio-card-ready.py"

echo '[test] speaker-only audio enhancement'
python3 "$repo_root/tests/speaker-enhancement.py"

echo '[test] silent audio acceptance harness (mocked)'
python3 "$repo_root/tests/audio-acceptance-mock.py"

echo '[test] private supervised audio matrix evaluator'
python3 "$repo_root/tests/pds018-audio-matrix-evaluate.py"

echo '[test] read-only fan acceptance collector (fixture)'
python3 "$repo_root/tests/fan-acceptance.py"

echo '[test] connected-idle fan override'
python3 "$repo_root/tests/fan-connected-idle.py"

echo '[test] private fan noise marker correlation (fixture)'
python3 "$repo_root/tests/pds009-fan-correlate.py"

echo '[test] power safety configuration'
python3 "$repo_root/tests/power-safety-config.py"

echo '[test] TuneD performance profile routing'
python3 "$repo_root/tests/power-profile-config.py"

echo '[test] live TuneD acceptance harness (mocked)'
python3 "$repo_root/tests/power-profile-live-mock.py"

echo '[test] Renesas USB wake policy'
"$repo_root/tests/usb-wake-policy-mock.sh"

echo '[test] PDS-023 private supervised USB wake matrix evaluator'
python3 "$repo_root/tests/pds023-usb-wake-evaluate.py"

echo '[test] USB MTP transfer scope'
python3 "$repo_root/tests/usb-transfer-config.py"

echo '[test] firewall zone policy'
python3 "$repo_root/tests/firewall-config.py"

echo '[test] PDS-013 privacy-minimal phone integration preflight'
python3 "$repo_root/tests/pds013-phone-integration-status.py"

echo '[test] network-online boot policy'
python3 "$repo_root/tests/network-online-policy.py"

echo '[test] DNF5 Pocket DS hardware update protection'
python3 "$repo_root/tests/update-policy.py"
"$repo_root/tests/update-protection-mock.sh"

echo '[test] DNF5 userspace-only stored transaction evaluator (mocked)'
python3 "$repo_root/tests/userspace-update-plan.py"

echo '[test] ES-DE config migration'
python3 "$repo_root/tests/esde-config-migration.py"

echo '[test] privacy-minimal ES-DE log summary'
python3 "$repo_root/tests/esde-log-summary.py"

echo '[test] ES-DE installer policy'
python3 "$repo_root/tests/emulation-install-policy.py"

echo '[test] bounded ROM metadata preflight'
python3 "$repo_root/tests/rom-preflight.py"

echo '[test] native ES-DE library and curated collection stage'
python3 "$repo_root/tests/esde-library-stage.py"

echo '[test] transferred ES-DE library inventory verification'
python3 "$repo_root/tests/esde-library-verify.py"

echo '[test] full Android/Linux shared library overlay'
python3 "$repo_root/tests/full-library-overlay.py"

echo '[test] isolated ES-DE synthetic acceptance fixture'
python3 "$repo_root/tests/esde-synthetic-acceptance.py"

echo '[test] fail-closed Chromium runtime lock'
python3 "$repo_root/tests/chromium-runtime-lock.py"

echo '[test] offline Chromium package provenance verifier'
python3 "$repo_root/tests/chromium-provenance-verify.py"

echo '[test] diagnostic privacy policy and redaction'
python3 "$repo_root/tests/diagnostic-privacy.py"

echo '[test] InputPlumber exact mode helper'
"$repo_root/tests/input-mode-mock.sh"

echo '[test] focused InputPlumber installer policy'
python3 "$repo_root/tests/input-install-policy.py"

echo '[test] read-only held-modifier observer'
python3 "$repo_root/tests/input-held-modifiers.py"

echo '[test] common game-session supervisor'
python3 "$repo_root/tests/game-session-supervisor-mock.py"

echo '[test] transactional game/input stack installer'
python3 "$repo_root/tests/game-input-stack-installer-mock.py"

echo '[test] thin ES-DE launcher routing'
"$repo_root/tests/emulation-launcher-mock.sh"

echo '[test] thin RetroArch standalone/nested routing'
"$repo_root/tests/retroarch-launcher-mock.sh"

echo '[test] melonDS dual-screen configuration'
python3 "$repo_root/tests/melonds-config.py"

echo '[test] melonDS ES-DE session routing'
"$repo_root/tests/melonds-launcher-mock.sh"

machine_model=''
if [[ -r /sys/firmware/devicetree/base/model ]]; then
    IFS= read -r -d '' machine_model \
        < /sys/firmware/devicetree/base/model || true
fi

if [[ -d /run/systemd/system && $machine_model == 'AYANEO Pocket DS' ]]; then
    echo '[test] service health'
    "$repo_root/tests/services-readonly.sh"
else
    echo '[test] service health skipped outside AYANEO Pocket DS hardware'
fi

echo '[test] PASS'
