#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
build_dir="$repo_root/build"
mkdir -p "$build_dir"

echo '[lint] Python'
PYTHONPYCACHEPREFIX="$build_dir/pycache" python3 -m py_compile \
    "$repo_root"/packaging/sd-image/*.py \
    "$repo_root"/scripts/pocketds-sd-image-*.py \
    "$repo_root/tests/sd-image-stage.py" \
    "$repo_root"/tests/test_sd_image_*.py
python3 -m py_compile "$repo_root/scripts/controller_update_services.py" \
    "$repo_root/tests/panel-cache-isolation.py"
PYTHONPYCACHEPREFIX="$build_dir/pycache" \
    python3 -m py_compile \
    "$repo_root/scripts/render-update-protection.py" \
    "$repo_root/scripts/export-source-preview.py" \
    "$repo_root/tests/source-preview.py" \
    "$repo_root/tests/third-party-source-pins.py" \
    "$repo_root/tests/inputplumber-mouse-clear-contract.py" \
    "$repo_root/tests/boot-switch-result.py" \
    "$repo_root/tests/brightness-daemon.py" \
    "$repo_root/tests/daily-suspend-install-transaction.py" \
    "$repo_root/tests/install-service-upgrade.py" \
    "$repo_root/components/keyboard/pocketds-keyboard.py" \
    "$repo_root/components/keyboard/keyboard_adapter.py" \
    "$repo_root/components/keyboard/visibility_state.py" \
    "$repo_root/components/keyboard/screen_geometry.py" \
    "$repo_root/components/keyboard/voice_artifacts.py" \
    "$repo_root/components/touchpad/pocketds-touchpad.py" \
    "$repo_root/components/touchpad/touchpad_gestures.py" \
    "$repo_root/components/touchpad/touchpad_raw.py" \
    "$repo_root/components/codex-quota/pocketds-codex-quota" \
    "$repo_root/components/fan/pocketds-fancontrol-connected-idle.py" \
    "$repo_root/components/brightness/pocketds-brightness.py" \
    "$repo_root/components/gamepad-activity/pocketds-gamepad-activity.py" \
    "$repo_root/tools/gamepad-activity/wayland-idle-probe.py" \
    "$repo_root/tools/gamepad-activity/verify-idle-reset.py" \
    "$repo_root/tests/gamepad-activity.py" \
    "$repo_root/components/audio/pocketds-speaker-enhancement.py" \
    "$repo_root/components/audio/pocketds-audio-card-ready.py" \
    "$repo_root/components/system/pocketds-deep-suspend.py" \
    "$repo_root/components/system/pocketds-daily-suspend.py" \
    "$repo_root/tools/kernel-ab/install-dsc-v4.py" \
    "$repo_root/tests/dsc-v4-install.py" \
    "$repo_root/tests/daily-suspend.py" \
    "$repo_root/components/system/pocketds-lid-mode.py" \
    "$repo_root/tests/lid-mode.py" \
    "$repo_root/scripts/check-libinput-lid.py" \
    "$repo_root/scripts/install-libinput-lid.py" \
    "$repo_root/tests/libinput-lid.py" \
    "$repo_root/tests/panel-lid-actions.py" \
    "$repo_root/components/system/pocketds-boot-mode.py" \
    "$repo_root/components/emulation/pocketds-es-de-prepare.py" \
    "$repo_root/components/emulation/pocketds-game-session.py" \
    "$repo_root/components/telemetry/pocketds-gpu-telemetry.py" \
    "$repo_root/scripts/pocketds-gpu-observer.py" \
    "$repo_root/scripts/pds001-deep-cycle.py" \
    "$repo_root/scripts/pds011-asr-evaluate.py" \
    "$repo_root/scripts/pds011-sensevoice-prepare.py" \
    "$repo_root/scripts/pds002-gpu-kwin-baseline.py" \
    "$repo_root/scripts/pocketds-diagnostic-redact.py" \
    "$repo_root/scripts/pocketds-rom-preflight.py" \
    "$repo_root/scripts/pocketds-esde-library-stage.py" \
    "$repo_root/scripts/pocketds-esde-library-verify.py" \
    "$repo_root/scripts/pocketds-esde-synthetic-acceptance.py" \
    "$repo_root/scripts/pocketds-chromium-provenance-verify.py" \
    "$repo_root/scripts/pocketds-chromium-runtime-verify.py" \
    "$repo_root/scripts/pocketds-audio-acceptance.py" \
    "$repo_root/scripts/restore-chinese-catalogs.py" \
    "$repo_root/scripts/pocketds-fan-acceptance.py" \
    "$repo_root/scripts/pocketds-input-held-modifiers.py" \
    "$repo_root/scripts/pds010-wiliwili-attended.py" \
    "$repo_root/scripts/install-game-input-stack.py" \
    "$repo_root/scripts/pocketds-inputplumber-haptics.py" \
    "$repo_root/scripts/pocketds-codex-quota-verify.py" \
    "$repo_root/scripts/pocketds-plasma-recovery.py" \
    "$repo_root/scripts/pds005-ui-input-evaluate.py" \
    "$repo_root/scripts/pds004-x11-probe-client.py" \
    "$repo_root/scripts/pds004-xwayland-isolation.py" \
    "$repo_root/scripts/pds008-telemetry-soak.py" \
    "$repo_root/scripts/pds012-esde-log-summary.py" \
    "$repo_root/scripts/pds013-phone-integration-status.py" \
    "$repo_root/scripts/pds014-post-boot-acceptance.py" \
    "$repo_root/scripts/pds014-post-boot-series.py" \
    "$repo_root/scripts/pds020-asset-c2pa-verify.py" \
    "$repo_root/scripts/pds020-composition-preflight.py" \
    "$repo_root/scripts/pds020-install-asset-preflight.py" \
    "$repo_root/scripts/pds020-release-root-audit.py" \
    "$repo_root/scripts/pds020-spdx-offline-validate.py" \
    "$repo_root/scripts/pds020-userspace-rpm-audit.py" \
    "$repo_root/scripts/pds020-userspace-rpm-archive-verify.py" \
    "$repo_root/scripts/pds020-userspace-rpm-full-transaction.py" \
    "$repo_root/scripts/pds020-userspace-rpm-rootfs-transaction.py" \
    "$repo_root/scripts/pds020-userspace-rpm-repository-verify.py" \
    "$repo_root/scripts/pds020-userspace-rpm-repository-postsign.py" \
    "$repo_root/scripts/pds020-userspace-rpm-repository-smoke.py" \
    "$repo_root/scripts/pds020-userspace-srpm-prepare.py" \
    "$repo_root/scripts/pds023-usb-wake-evaluate.py" \
    "$repo_root/scripts/pocketds-userspace-update-plan.py" \
    "$repo_root/tools/kernel-ab/planner.py" \
    "$repo_root/tools/kernel-ab/attended-lid-cycle.py" \
    "$repo_root/tools/kernel-ab/observe-lid-cycle.py" \
    "$repo_root/tools/kernel-ab/attended-native-lid-cycle.py" \
    "$repo_root/tests/attended-native-lid-cycle.py" \
    "$repo_root/tests/attended-lid-cycle.py" \
    "$repo_root/tools/kernel-ab/evidence.py" \
    "$repo_root/tools/kernel-ab/source-evidence.py" \
    "$repo_root/tools/kernel-ab/reproducible-build/payload-evidence.py" \
    "$repo_root/tools/kernel-ab/candidate-build/ifpc-evidence.py" \
    "$repo_root/tools/kernel-ab/ab-evaluate.py" \
    "$repo_root/tools/kernel-ab/ab-ledger.py" \
    "$repo_root/tools/kernel-ab/workload/preflight.py" \
    "$repo_root/tools/kernel-ab/workload/run.py" \
    "$repo_root/tools/kernel-ab/rollback/preflight.py" \
    "$repo_root/tools/kernel-ab/runtime-boot/compose.py" \
    "$repo_root/tools/kernel-ab/recovery/preflight.py" \
    "$repo_root/tools/kernel-ab/recovery/runtime-attest.py" \
    "$repo_root/tools/kernel-ab/recovery/dispatch.py" \
    "$repo_root/tools/kernel-ab/recovery/evaluate.py" \
    "$repo_root/tools/kernel-ab/recovery/transaction.py" \
    "$repo_root/tests/esde-config-migration.py" \
    "$repo_root/tests/emulation-install-policy.py" \
    "$repo_root/tests/brightness-state-machine.py" \
    "$repo_root/tests/brightness-powerdevil.py" \
    "$repo_root/tests/keyboard-visibility-state.py" \
    "$repo_root/tests/keyboard-adapter.py" \
    "$repo_root/tests/keyboard-window-transfer.py" \
    "$repo_root/tests/keyboard-geometry.py" \
    "$repo_root/tests/voice-artifacts.py" \
    "$repo_root/scripts/pocketds-asr-api-provision.py" \
    "$repo_root/scripts/install-asr-api.sh" \
    "$repo_root/tests/asr-api.py" \
    "$repo_root/tests/asr-api-provision.py" \
    "$repo_root/tests/asr-api-install-transaction.py" \
    "$repo_root/tests/touchpad-gestures.py" \
    "$repo_root/tests/touchpad-raw.py" \
    "$repo_root/tests/touchpad-reader-lifecycle.py" \
    "$repo_root/tests/touchpad-button-ownership.py" \
    "$repo_root/tests/ui-design-contract.py" \
    "$repo_root/tests/joymouse-profile.py" \
    "$repo_root/components/inputplumber/controller_test_gate.py" \
    "$repo_root/components/controller-test/pocketds-controller-test.py" \
    "$repo_root/tests/controller-test-backend.py" \
    "$repo_root/tests/controller-test-gate.py" \
    "$repo_root/tests/controller-test-client.py" \
    "$repo_root/tests/controller-test-native.py" \
    "$repo_root/tests/inputplumber-haptics-contract.py" \
    "$repo_root/tests/inputplumber-haptics-installer.py" \
    "$repo_root/tests/game-session-supervisor-mock.py" \
    "$repo_root/tests/game-input-stack-installer-mock.py" \
    "$repo_root/scripts/pocketds-wiliwili-gamecontrollerdb.py" \
    "$repo_root/tests/wiliwili-gamecontrollerdb.py" \
    "$repo_root/tests/wiliwili-input-install-policy.py" \
    "$repo_root/tests/wiliwili-launcher-install-policy.py" \
    "$repo_root/tests/game-session-lock-policy.py" \
    "$repo_root/tests/pds010-wiliwili-attended.py" \
    "$repo_root/tests/input-held-modifiers.py" \
    "$repo_root/tests/input-install-policy.py" \
    "$repo_root/tests/gpu-observer-smoke.py" \
    "$repo_root/tests/gmu-runtime-policy.py" \
    "$repo_root/tests/pds002-baseline-smoke.py" \
    "$repo_root/tests/kernel-ab-planner.py" \
    "$repo_root/tests/kernel-ab-evidence.py" \
    "$repo_root/tests/kernel-source-evidence.py" \
    "$repo_root/tests/kernel-payload-reproduction.py" \
    "$repo_root/tests/kernel-ifpc-candidate.py" \
    "$repo_root/tests/kernel-ab-evaluate.py" \
    "$repo_root/tests/kernel-ab-ledger.py" \
    "$repo_root/tests/kernel-ab-workload.py" \
    "$repo_root/tests/kernel-ab-workload-run.py" \
    "$repo_root/tests/kernel-rollback-preflight.py" \
    "$repo_root/tests/kernel-runtime-boot-compose.py" \
    "$repo_root/tests/kernel-recovery-host-preflight.py" \
    "$repo_root/tests/kernel-recovery-runtime-attest.py" \
    "$repo_root/tests/kernel-recovery-dispatch.py" \
    "$repo_root/tests/kernel-recovery-evaluate.py" \
    "$repo_root/tests/kernel-recovery-transaction.py" \
    "$repo_root/tests/diagnostic-privacy.py" \
    "$repo_root/tests/rom-preflight.py" \
    "$repo_root/tests/esde-library-stage.py" \
    "$repo_root/tests/esde-library-verify.py" \
    "$repo_root/tests/esde-synthetic-acceptance.py" \
    "$repo_root/tests/chromium-provenance-verify.py" \
    "$repo_root/tests/chromium-runtime-lock.py" \
    "$repo_root/tests/gpu-telemetry-smoke.py" \
    "$repo_root/tests/display-telemetry.py" \
    "$repo_root/tests/audio-routing-config.py" \
    "$repo_root/tests/audio-card-ready.py" \
    "$repo_root/tests/speaker-enhancement.py" \
    "$repo_root/tests/firewall-config.py" \
    "$repo_root/tests/network-online-policy.py" \
    "$repo_root/tests/power-safety-config.py" \
    "$repo_root/tests/power-profile-config.py" \
    "$repo_root/tests/fan-connected-idle.py" \
    "$repo_root/tests/power-profile-live-mock.py" \
    "$repo_root/tests/panel-battery-fixture.py" \
    "$repo_root/experiments/pds005-panel-shell/migration/applet_migration.py" \
    "$repo_root/experiments/pds005-panel-shell/migration/panel_geometry.py" \
    "$repo_root/tests/pds005-applet-migration.py" \
    "$repo_root/tests/pds005-panel-geometry.py" \
    "$repo_root/tests/pds005-plasma-recovery.py" \
    "$repo_root/tests/pds005-panel-actions.py" \
    "$repo_root/tests/codex-quota.py" \
    "$repo_root/tests/codex-quota-verify.py" \
    "$repo_root/tests/codex-quota-install-policy.py" \
    "$repo_root/tests/install-privilege-order.py" \
    "$repo_root/tests/pds005-ui-input-evaluate.py" \
    "$repo_root/tests/pds004-xwayland-isolation.py" \
    "$repo_root/tests/pds001-deep-cycle.py" \
    "$repo_root/tests/deep-suspend-guard.py" \
    "$repo_root/tests/boot-mode-switch.py" \
    "$repo_root/tests/android-boot-switch-source.py" \
    "$repo_root/tests/shared-game-library-stage.py" \
    "$repo_root/tests/pds011-asr-evaluate.py" \
    "$repo_root/tests/pds011-sensevoice-prepare.py" \
    "$repo_root/tests/pds008-telemetry-soak.py" \
    "$repo_root/tests/esde-log-summary.py" \
    "$repo_root/tests/pds013-phone-integration-status.py" \
    "$repo_root/tests/pds014-post-boot-acceptance.py" \
    "$repo_root/tests/pds014-post-boot-series.py" \
    "$repo_root/tests/pds020-asset-c2pa-verify.py" \
    "$repo_root/tests/pds020-composition-preflight.py" \
    "$repo_root/tests/pds020-install-asset-preflight.py" \
    "$repo_root/tests/pds020-release-root-audit.py" \
    "$repo_root/tests/pds020-spdx-offline-validate.py" \
    "$repo_root/tests/pds020-userspace-rpm-audit.py" \
    "$repo_root/tests/pds020-userspace-rpm-archive-verify.py" \
    "$repo_root/tests/pds020-userspace-rpm-full-transaction.py" \
    "$repo_root/tests/pds020-userspace-rpm-rootfs-transaction.py" \
    "$repo_root/tests/pds020-userspace-rpm-repository-verify.py" \
    "$repo_root/tests/pds020-userspace-rpm-repository-postsign.py" \
    "$repo_root/tests/pds020-userspace-rpm-repository-smoke.py" \
    "$repo_root/tests/pds020-userspace-srpm-prepare.py" \
    "$repo_root/tests/pds023-usb-wake-evaluate.py" \
    "$repo_root/tests/userspace-update-plan.py" \
    "$repo_root/tests/pds005-standalone-contract.py" \
    "$repo_root/tests/audio-acceptance-mock.py" \
    "$repo_root/tests/chinese-catalog-restore.py" \
    "$repo_root/tests/chinese-input-install-policy.py" \
    "$repo_root/tests/fan-acceptance.py"

echo '[lint] C++'
c++ -std=c++17 -Wall -Wextra -fsyntax-only \
    "$repo_root/components/control-panel/pocketds-panelctl.cpp"

echo '[lint] JSON/XML'
python3 -m json.tool \
    "$repo_root/tools/kernel-ab/evidence-spec.json" >/dev/null
python3 -m json.tool \
    "$repo_root/tools/kernel-ab/source-evidence-spec.json" >/dev/null
python3 -m json.tool \
    "$repo_root/tools/kernel-ab/reproducible-build/lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/tools/kernel-ab/candidate-build/ifpc-lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/tools/kernel-ab/rollback/lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/tools/kernel-ab/runtime-boot/lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/tools/kernel-ab/recovery/lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/control-panel/plasmoid/metadata.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/chromium/runtime-lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/locale/rime-ice.lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/keyboard/sensevoice-artifacts-lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/inputplumber/inputplumber-haptics-source-lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/chromium/provenance/receipt.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/source-lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/source-lock.pds2.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/build-lock.pds2.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/dependency-archives-lock.pds2.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/rootfs-package-archives-lock.pds2.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/repository-metadata-lock.pds2.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/pocketds-userspace/full-transaction-lock.pds2.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/image/composition-lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/packaging/image/spdx-validation-lock.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/assets/provenance/c2pa-receipt.json" >/dev/null
python3 -m json.tool \
    "$repo_root/components/assets/provenance/c2patool-settings.json" >/dev/null
if command -v xmllint >/dev/null 2>&1; then
    xmllint --noout "$repo_root/components/emulation/es_settings.xml"
    xmllint --noout "$repo_root/components/emulation/es_systems.xml"
    xmllint --noout "$repo_root/components/emulation/es_systems-shared.xml"
    xmllint --noout "$repo_root/components/game-runtime/gamescope-control.xml"
fi

echo '[lint] shell'
while IFS= read -r script; do
    case "$(head -n 1 "$script")" in
        *bash*) bash -n "$script" ;;
        *'/sh') sh -n "$script" ;;
        *) continue ;;
    esac
done < <(find "$repo_root/components" "$repo_root/scripts" "$repo_root/tests" \
    "$repo_root/tools/kernel-ab/reproducible-build" \
    "$repo_root/tools/kernel-ab/candidate-build" \
    "$repo_root/tools/kernel-ab/rollback" \
    "$repo_root/tools/kernel-ab/runtime-boot" \
    "$repo_root/tools/kernel-ab/recovery" -type f \
    \( -name '*.sh' -o -perm -u+x \) | sort)

if command -v desktop-file-validate >/dev/null 2>&1; then
    echo '[lint] desktop entries'
    find "$repo_root/components" -type f -name '*.desktop' -print0 |
        xargs -0 -r desktop-file-validate
fi

echo '[lint] credentials'
if command -v rg >/dev/null 2>&1; then
    if rg -n -i \
        'BEGIN .*PRIVATE KEY|privatekey[[:space:]]*=|presharedkey[[:space:]]*=|client_secret[[:space:]]*=|authorization:[[:space:]]*bearer' \
        "$repo_root" --glob '!**/build/**' --glob '!**/.git/**' --glob '!**/scripts/lint.sh'; then
        echo 'ERROR: possible credential material found' >&2
        exit 1
    fi
else
    if grep -R -n -E \
        'BEGIN .*PRIVATE KEY|[Pp]rivate[Kk]ey[[:space:]]*=|[Pp]reshared[Kk]ey[[:space:]]*=' \
        --exclude=lint.sh --exclude-dir=.git --exclude-dir=build "$repo_root"; then
        echo 'ERROR: possible credential material found' >&2
        exit 1
    fi
fi

if git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$repo_root" diff --check
fi

echo '[lint] OK'
