.PHONY: check lint test test-update-policy test-panel-geometry test-plasma-recovery test-panel-actions test-ui-deployment test-ui-update test-ui-input-matrix test-xwayland-isolation test-asr-runtime test-telemetry-soak test-battery-matrix test-usb-wake-matrix test-phone-integration test-deep-cycle test-deep-series test-asr-evaluate test-fan-correlate test-audio-matrix test-post-boot test-post-boot-series test-kernel-evidence test-kernel-source-evidence test-kernel-payload-reproduction test-kernel-ifpc-candidate test-kernel-ab-ledger test-kernel-ab-evaluate test-kernel-rollback-preflight test-kernel-runtime-boot test-kernel-recovery test-source-license-inventory test-spdx-offline test-release-audit test-userspace-rpm-repro test-userspace-rpm-postsign test-userspace-rpm-transaction test-hardware test-audio test-fan test-suspend test-emulation test-emulation-synthetic test-emulation-synthetic-gui test-power-live test-power-stress-live verify-chromium-runtime verify-chromium-provenance verify-kernel-source-evidence verify-kernel-payload-reproduction verify-kernel-ifpc-candidate verify-kernel-rollback-preflight compose-kernel-runtime-boot verify-kernel-runtime-boot preflight-kernel-recovery plan-kernel-boot-alias stage-kernel-candidate restore-kernel-baseline plan-kernel-recovery dispatch-kernel-recovery attest-kernel-recovery evaluate-kernel-recovery prepare-kernel-ab-ledger evaluate-kernel-ab verify-userspace-source audit-userspace-rpm verify-userspace-rpm-reproduction verify-userspace-rpm-postsign plan-userspace-rpm-transaction run-userspace-rpm-transaction verify-asset-c2pa preflight-asset-profile preflight-composition verify-spdx-offline audit-release-root inventory-source-licenses preflight-roms observe-fan mark-fan-noise analyze-fan-noise observe-gpu observe-gpu-baseline observe-kernel-evidence observe-telemetry-soak observe-phone-integration observe-emulation-log observe-deep-preflight evaluate-deep-series evaluate-asr evaluate-audio-matrix prepare-battery-ledger evaluate-battery-matrix prepare-usb-wake-ledger evaluate-usb-wake-matrix prepare-ui-input-ledger evaluate-ui-input plan-xwayland-isolation accept-xwayland-isolation audit-ui-deployment audit-ui-deployment-candidate plan-ui-update stage-ui-update apply-ui-update verify-ui-update rollback-ui-update post-boot-plan accept-post-boot evaluate-post-boot-series diagnostic-bundle install install-all install-brightness install-codex-quota install-emulation install-game-input-stack install-telemetry import-live status
.PHONY: test-kernel-ab-workload test-kernel-ab-workload-run preflight-kernel-ab-workload preflight-kernel-ab-run plan-kernel-ab-run run-kernel-ab-cell
.PHONY: test-userspace-rpm-archive test-userspace-rpm-full-transaction test-userspace-rpm-rootfs-transaction test-userspace-rpm-repository test-userspace-rpm-repository-postsign test-userspace-rpm-repository-smoke verify-userspace-rpm-archive verify-userspace-rpm-rootfs-archive verify-userspace-rpm-repository verify-userspace-rpm-repository-postsign plan-userspace-rpm-full-transaction run-userspace-rpm-full-transaction plan-userspace-rpm-rootfs-transaction run-userspace-rpm-rootfs-transaction plan-userspace-rpm-repository-smoke run-userspace-rpm-repository-smoke
.PHONY: verify-userspace-rpm-postsign-pds2 test-rootfs-smoke plan-rootfs-smoke run-rootfs-smoke
.PHONY: test-moonlight-fps test-steam-game-fps install-moonlight-fps
.PHONY: test-asr-prepare test-battery-charge-unit test-userspace-update-plan
.PHONY: test-usb-transfer-config
.PHONY: test-source-preview export-source-preview
.PHONY: test-wiliwili-attended accept-wiliwili-controls test-chinese-input install-chinese-input

check:
	./scripts/check.sh

lint:
	./scripts/lint.sh

test:
	./scripts/test.sh

.PHONY: test-sd-image
test-sd-image:
	python3 ./tests/sd-image-stage.py
	python3 -m unittest discover -s tests -p 'test_sd_image_*.py'

test-panel-geometry:
	python3 ./tests/pds005-panel-geometry.py

test-update-policy:
	python3 ./tests/update-policy.py

test-userspace-update-plan:
	python3 ./tests/userspace-update-plan.py

test-chinese-input:
	python3 ./tests/chinese-input-install-policy.py
	python3 ./tests/chinese-catalog-restore.py

test-plasma-recovery:
	python3 ./tests/pds005-plasma-recovery.py

test-panel-actions:
	python3 ./tests/pds005-panel-actions.py

test-ui-deployment:
	python3 ./tests/pds005-ui-deployment-audit.py

test-ui-update:
	python3 ./tests/pds005-ui-update-transaction.py

test-ui-input-matrix:
	python3 ./tests/pds005-ui-input-evaluate.py

test-wiliwili-attended:
	python3 ./tests/pds010-wiliwili-attended.py

accept-wiliwili-controls:
	python3 ./scripts/pds010-wiliwili-attended.py \
		--controls "$${CONTROLS:-a,b,x,y}" \
		--rounds "$${ROUNDS:-3}" \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-OBSERVE-WILIWILI-CONTROLS}"

test-xwayland-isolation:
	python3 ./tests/pds004-xwayland-isolation.py

test-asr-runtime:
	python3 ./tests/voice-artifacts.py
	python3 ./tests/asr-api.py
	python3 ./tests/asr-api-provision.py
	python3 ./tests/asr-api-install-transaction.py

test-asr-prepare:
	python3 ./tests/pds011-sensevoice-prepare.py

test-telemetry-soak:
	python3 ./tests/pds008-telemetry-soak.py

test-moonlight-fps:
	./tests/moonlight-fps-hook.sh
	./tests/moonlight-fps-hook-install.sh

test-steam-game-fps:
	./tests/steam-game-fps-wrapper.sh

test-battery-matrix:
	python3 ./tests/pds006-battery-matrix-evaluate.py

test-battery-charge-unit:
	python3 ./tests/pds017-battery-charge-unit.py

test-usb-wake-matrix:
	python3 ./tests/pds023-usb-wake-evaluate.py

test-usb-transfer-config:
	python3 ./tests/usb-transfer-config.py

test-phone-integration:
	python3 ./tests/pds013-phone-integration-status.py

test-deep-cycle:
	python3 ./tests/deep-suspend-guard.py
	python3 ./tests/pds001-deep-cycle.py

test-deep-series:
	python3 ./tests/pds001-deep-series.py

test-asr-evaluate:
	python3 ./tests/pds011-asr-evaluate.py

test-fan-correlate:
	python3 ./tests/pds009-fan-correlate.py

test-audio-matrix:
	python3 ./tests/pds018-audio-matrix-evaluate.py

test-post-boot:
	python3 ./tests/pds014-post-boot-acceptance.py

test-post-boot-series:
	python3 ./tests/pds014-post-boot-series.py

test-kernel-evidence:
	python3 ./tests/kernel-ab-evidence.py

test-kernel-source-evidence:
	python3 ./tests/kernel-source-evidence.py

test-kernel-payload-reproduction:
	python3 ./tests/kernel-payload-reproduction.py

test-kernel-ifpc-candidate:
	python3 ./tests/kernel-ifpc-candidate.py

test-kernel-ab-evaluate:
	python3 ./tests/kernel-ab-evaluate.py

test-kernel-ab-ledger:
	python3 ./tests/kernel-ab-ledger.py

test-kernel-ab-workload:
	python3 ./tests/kernel-ab-workload.py

test-kernel-ab-workload-run:
	python3 ./tests/kernel-ab-workload-run.py

test-userspace-rpm-repro:
	python3 ./tests/pds020-userspace-rpm-repro.py

test-userspace-rpm-postsign:
	python3 ./tests/pds020-userspace-rpm-postsign.py

test-userspace-rpm-transaction:
	python3 ./tests/pds020-userspace-rpm-transaction.py

test-userspace-rpm-archive:
	python3 ./tests/pds020-userspace-rpm-archive-verify.py

test-userspace-rpm-full-transaction:
	python3 ./tests/pds020-userspace-rpm-full-transaction.py

test-userspace-rpm-rootfs-transaction:
	python3 ./tests/pds020-userspace-rpm-rootfs-transaction.py

test-userspace-rpm-repository:
	python3 ./tests/pds020-userspace-rpm-repository-verify.py

test-userspace-rpm-repository-postsign:
	python3 ./tests/pds020-userspace-rpm-repository-postsign.py

test-userspace-rpm-repository-smoke:
	python3 ./tests/pds020-userspace-rpm-repository-smoke.py

test-kernel-rollback-preflight:
	python3 ./tests/kernel-rollback-preflight.py

test-kernel-runtime-boot:
	python3 ./tests/kernel-runtime-boot-compose.py

test-kernel-recovery:
	python3 ./tests/kernel-recovery-host-preflight.py
	python3 ./tests/kernel-recovery-runtime-attest.py
	python3 ./tests/kernel-recovery-dispatch.py
	python3 ./tests/kernel-recovery-evaluate.py
	python3 ./tests/kernel-recovery-transaction.py

test-source-license-inventory:
	python3 ./tests/pds020-source-license-inventory.py

test-spdx-offline:
	python3 ./tests/pds020-spdx-offline-validate.py

test-release-audit:
	python3 ./tests/pds020-release-root-audit.py

test-rootfs-smoke:
	python3 ./tests/pds020-rootfs-smoke.py

test-hardware:
	./tests/hardware-readonly.sh

test-audio:
	./scripts/pocketds-audio-acceptance.py

test-fan:
	python3 ./tests/fan-acceptance.py
	python3 ./tests/fan-connected-idle.py

test-suspend:
	./tests/suspend-preflight.sh

test-emulation:
	./tests/emulation-readonly.sh

test-emulation-synthetic:
	python3 ./scripts/pocketds-esde-synthetic-acceptance.py --prepare-only

test-emulation-synthetic-gui:
	POCKETDS_ALLOW_ESDE_GUI_TEST="$${POCKETDS_ALLOW_ESDE_GUI_TEST:?set to YES}" \
	python3 ./scripts/pocketds-esde-synthetic-acceptance.py \
		--run-gui \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-RUN-ESDE-SYNTHETIC-GUI}" \
		--output "$${OUTPUT:?set OUTPUT to a new private GUI report}"

test-power-live:
	./tests/power-profile-live.sh --apply

test-power-stress-live:
	POCKETDS_ALLOW_POWER_STRESS="$${POCKETDS_ALLOW_POWER_STRESS:?set to YES}" \
	./tests/power-profile-live.sh --apply --stress-100 \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-POWER-100}"

verify-chromium-runtime:
	python3 ./scripts/pocketds-chromium-runtime-verify.py

verify-chromium-provenance:
	python3 ./scripts/pocketds-chromium-provenance-verify.py \
		--archive-dir "$${ARCHIVE_DIR:?set ARCHIVE_DIR to the directory containing the five pinned package archives}" \
		--sqv "$${SQV:?set SQV to a trusted sqv executable}"

verify-userspace-source:
	python3 ./scripts/pds020-userspace-srpm-prepare.py \
		--source-rpm "$${SOURCE_RPM:?set SOURCE_RPM to the pinned pocketds-userspace source RPM}" \
		--extracted-dir "$${EXTRACTED_DIR:?set EXTRACTED_DIR to a new extracted source-RPM directory}"

audit-userspace-rpm:
	python3 ./scripts/pds020-userspace-rpm-audit.py \
		--binary-rpm "$${BINARY_RPM:?set BINARY_RPM to the hardened pre-sign binary RPM}"

verify-userspace-rpm-reproduction:
	python3 ./scripts/pds020-userspace-rpm-repro.py \
		--result-a "$${RESULT_A:?set RESULT_A to the first private offline mock result directory}" \
		--result-b "$${RESULT_B:?set RESULT_B to the second private offline mock result directory}"

verify-userspace-rpm-postsign:
	python3 ./scripts/pds020-userspace-rpm-postsign.py \
		--receipt "$${RECEIPT:?set RECEIPT to the private post-sign JSON receipt}" \
		--artifact "$${SIGNED_RPM:?set SIGNED_RPM to the exact signed hardened RPM}" \
		--public-key "$${RELEASE_PUBLIC_KEY:?set RELEASE_PUBLIC_KEY to the exact public certificate}"

verify-userspace-rpm-postsign-pds2:
	python3 ./scripts/pds020-userspace-rpm-postsign.py \
		--build-lock ./packaging/pocketds-userspace/build-lock.pds2.json \
		--source-lock ./packaging/pocketds-userspace/source-lock.pds2.json \
		--receipt "$${RECEIPT:?set RECEIPT to the private pds2 post-sign JSON receipt}" \
		--artifact "$${SIGNED_RPM:?set SIGNED_RPM to the exact signed pds2 RPM}" \
		--public-key "$${RELEASE_PUBLIC_KEY:?set RELEASE_PUBLIC_KEY to the exact public certificate}"

verify-userspace-rpm-archive:
	python3 ./scripts/pds020-userspace-rpm-archive-verify.py \
		--archive-dir "$${ARCHIVE_DIR:?set ARCHIVE_DIR to the exact mode-0700 pds2 dependency archive}"

verify-userspace-rpm-rootfs-archive:
	python3 ./scripts/pds020-userspace-rpm-archive-verify.py \
		--archive-lock ./packaging/pocketds-userspace/rootfs-package-archives-lock.pds2.json \
		--archive-dir "$${ARCHIVE_DIR:?set ARCHIVE_DIR to the exact mode-0700 pds2 rootfs package archive}"

verify-userspace-rpm-repository:
	python3 ./scripts/pds020-userspace-rpm-repository-verify.py \
		--archive-dir "$${ARCHIVE_DIR:?set ARCHIVE_DIR to the exact mode-0700 pds2 rootfs package archive}" \
		--result-a "$${RESULT_A:?set RESULT_A to the first private repodata directory}" \
		--result-b "$${RESULT_B:?set RESULT_B to the second private repodata directory}"

verify-userspace-rpm-repository-postsign:
	python3 ./scripts/pds020-userspace-rpm-repository-postsign.py \
		--receipt "$${RECEIPT:?set RECEIPT to the private final-repository receipt}" \
		--rpm-postsign-receipt "$${RPM_POSTSIGN_RECEIPT:?set RPM_POSTSIGN_RECEIPT to the private pds2 RPM receipt}" \
		--signed-rpm "$${SIGNED_RPM:?set SIGNED_RPM to the exact signed pds2 RPM}" \
		--public-key "$${RELEASE_PUBLIC_KEY:?set RELEASE_PUBLIC_KEY to the exact public certificate}" \
		--signature "$${REPOMD_SIGNATURE:?set REPOMD_SIGNATURE to RESULT_A/repomd.xml.asc}" \
		--unsigned-archive-dir "$${UNSIGNED_ARCHIVE_DIR:?set UNSIGNED_ARCHIVE_DIR to the verified pre-sign archive}" \
		--signed-archive-dir "$${SIGNED_ARCHIVE_DIR:?set SIGNED_ARCHIVE_DIR to the exact final archive}" \
		--result-a "$${RESULT_A:?set RESULT_A to the first final repodata directory}" \
		--result-b "$${RESULT_B:?set RESULT_B to the second final repodata directory}"

plan-userspace-rpm-repository-smoke:
	python3 ./scripts/pds020-userspace-rpm-repository-smoke.py

run-userspace-rpm-repository-smoke:
	python3 ./scripts/pds020-userspace-rpm-repository-smoke.py \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to the exact repository smoke phrase}" \
		--work-dir "$${WORK_DIR:?set WORK_DIR to a private empty-root workspace}" \
		--output "$${OUTPUT:?set OUTPUT to a new private result path}" \
		--receipt "$${RECEIPT:?set RECEIPT to the private final-repository receipt}" \
		--rpm-postsign-receipt "$${RPM_POSTSIGN_RECEIPT:?set RPM_POSTSIGN_RECEIPT to the private pds2 RPM receipt}" \
		--signed-rpm "$${SIGNED_RPM:?set SIGNED_RPM to the exact signed pds2 RPM}" \
		--public-key "$${RELEASE_PUBLIC_KEY:?set RELEASE_PUBLIC_KEY to the exact public certificate}" \
		--signature "$${REPOMD_SIGNATURE:?set REPOMD_SIGNATURE to RESULT_A/repomd.xml.asc}" \
		--unsigned-archive-dir "$${UNSIGNED_ARCHIVE_DIR:?set UNSIGNED_ARCHIVE_DIR to the verified pre-sign archive}" \
		--signed-archive-dir "$${SIGNED_ARCHIVE_DIR:?set SIGNED_ARCHIVE_DIR to the exact final archive}" \
		--result-a "$${RESULT_A:?set RESULT_A to the first final repodata directory}" \
		--result-b "$${RESULT_B:?set RESULT_B to the second final repodata directory}"

plan-userspace-rpm-transaction:
	python3 ./scripts/pds020-userspace-rpm-transaction.py

run-userspace-rpm-transaction:
	python3 ./scripts/pds020-userspace-rpm-transaction.py \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to the exact displayed confirmation}" \
		--vendor-rpm "$${VENDOR_RPM:?set VENDOR_RPM to the exact locked vendor baseline}" \
		--hardened-rpm "$${HARDENED_RPM:?set HARDENED_RPM to the exact canonical hardened RPM}" \
		--output "$${OUTPUT:?set OUTPUT to a new file in a private mode-0700 directory}"

plan-userspace-rpm-full-transaction:
	python3 ./scripts/pds020-userspace-rpm-full-transaction.py

run-userspace-rpm-full-transaction:
	python3 ./scripts/pds020-userspace-rpm-full-transaction.py \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to the exact displayed confirmation}" \
		--archive-dir "$${ARCHIVE_DIR:?set ARCHIVE_DIR to the exact mode-0700 pds2 dependency archive}" \
		--output "$${OUTPUT:?set OUTPUT to a new file in a private mode-0700 directory}"

plan-userspace-rpm-rootfs-transaction:
	python3 ./scripts/pds020-userspace-rpm-rootfs-transaction.py

run-userspace-rpm-rootfs-transaction:
	python3 ./scripts/pds020-userspace-rpm-rootfs-transaction.py \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to the exact displayed confirmation}" \
		--archive-dir "$${ARCHIVE_DIR:?set ARCHIVE_DIR to the exact mode-0700 pds2 rootfs package archive}" \
		--work-dir "$${WORK_DIR:?set WORK_DIR to an empty private mode-0700 work directory}" \
		--output "$${OUTPUT:?set OUTPUT to a new file in a private mode-0700 directory}"

verify-asset-c2pa:
	python3 ./scripts/pds020-asset-c2pa-verify.py \
		--c2patool "$${C2PATOOL:?set C2PATOOL to the pinned c2patool executable}" \
		--signer-trust-list "$${C2PA_TRUST_LIST:?set C2PA_TRUST_LIST to the pinned signer trust list}" \
		--tsa-trust-list "$${C2PA_TSA_TRUST_LIST:?set C2PA_TSA_TRUST_LIST to the pinned TSA trust list}"

preflight-asset-profile:
	python3 ./scripts/pds020-install-asset-preflight.py \
		--profile "$${ASSET_PROFILE:?set ASSET_PROFILE to personal-assets or asset-free}"

preflight-composition:
	python3 ./scripts/pds020-composition-preflight.py

plan-rootfs-smoke:
	python3 ./scripts/pds020-rootfs-smoke.py

run-rootfs-smoke:
	python3 ./scripts/pds020-rootfs-smoke.py \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to the exact displayed confirmation}" \
		--root "$${RELEASE_ROOT:?set RELEASE_ROOT to the offline mounted/extracted root}" \
		--artifact "$${ROOTFS_ARTIFACT:?set ROOTFS_ARTIFACT to the matching container.tar}" \
		--output "$${OUTPUT:?set OUTPUT to a new file in a private directory}"

audit-release-root:
	./scripts/pds020-release-root-audit.py --root "$${RELEASE_ROOT:?set RELEASE_ROOT to an offline mounted image root}"

inventory-source-licenses:
	./scripts/pds020-source-license-inventory.py --output "$${OUTPUT:--}"

verify-spdx-offline:
	./scripts/pds020-spdx-offline-validate.py \
		--sbom "$${SBOM:?set SBOM to the final SPDX 3.0.1 JSON-LD document}" \
		--context "$${SPDX_CONTEXT:?set SPDX_CONTEXT to the locked offline context}" \
		--json-schema "$${SPDX_JSON_SCHEMA:?set SPDX_JSON_SCHEMA to the locked offline schema}" \
		--semantic-model "$${SPDX_MODEL:?set SPDX_MODEL to the locked offline OWL/SHACL model}" \
		--output "$${OUTPUT:--}"

preflight-roms:
	./scripts/pocketds-rom-preflight.py "$${ROM_ROOT:-$$HOME/ROMs}"

observe-fan:
	./scripts/pocketds-fan-acceptance.py --samples $${SAMPLES:-80} --interval $${INTERVAL:-1.5} --output "$${OUTPUT:--}"

mark-fan-noise:
	./scripts/pds009-fan-correlate.py mark \
		--markers "$${MARKERS:?set MARKERS to a private marker JSONL path}"

analyze-fan-noise:
	./scripts/pds009-fan-correlate.py analyze \
		--evidence "$${EVIDENCE:?set EVIDENCE to a private fan collector JSONL path}" \
		--markers "$${MARKERS:?set MARKERS to its private marker JSONL path}" \
		--output "$${OUTPUT:--}" \
		--max-offset-ms "$${MAX_OFFSET_MS:-3000}"

observe-gpu:
	./scripts/pocketds-gpu-observer.py --duration $${DURATION:-1800} --interval $${INTERVAL:-2}

observe-gpu-baseline:
	./scripts/pds002-gpu-kwin-baseline.py --duration $${DURATION:-2700} --interval $${INTERVAL:-5} --repo-revision "$$(git rev-parse HEAD)"

observe-kernel-evidence:
	./tools/kernel-ab/evidence.py --pretty

verify-kernel-source-evidence:
	./tools/kernel-ab/source-evidence.py \
		--repomd "$${REPOMD:?set REPOMD to the pinned COPR repomd.xml}" \
		--primary "$${PRIMARY:?set PRIMARY to the pinned COPR primary.xml.gz}" \
		--binary-rpm "$${BINARY_RPM:?set BINARY_RPM to the pinned binary RPM}" \
		--source-rpm "$${SOURCE_RPM:?set SOURCE_RPM to the pinned source RPM}" \
		--srpm-source-archive "$${SRPM_SOURCE_ARCHIVE:?set SRPM_SOURCE_ARCHIVE to linux-7.1-rc2.tar.gz from the SRPM}" \
		--commit-archive "$${COMMIT_ARCHIVE:?set COMMIT_ARCHIVE to the pinned GitLab commit archive}" \
		--lid-source-patch ./tools/kernel-ab/patches/0001-arm64-dts-qcom-pocketds-native-lid.patch \
		--copr-keyring "$${COPR_KEYRING:?set COPR_KEYRING to the pinned COPR public key}" \
		--sqv "$${SQV:?set SQV to the pinned Sequoia sqv verifier}" \
		--rebuilt-dtb "$${REBUILT_DTB:?set REBUILT_DTB to the independently rebuilt native-lid DTB}" \
		--pretty

verify-kernel-payload-reproduction:
	./tools/kernel-ab/reproducible-build/payload-evidence.py \
		--artifact-dir "$${ARTIFACT_DIR:?set ARTIFACT_DIR to the locked build artifact directory}" \
		--reference-root "$${REFERENCE_ROOT:?set REFERENCE_ROOT to a new extraction of the signed RPM}" \
		--rebuilt-root "$${REBUILT_ROOT:?set REBUILT_ROOT to a new extraction of the attested rebuild RPM}" \
		--pretty

verify-kernel-ifpc-candidate:
	./tools/kernel-ab/candidate-build/ifpc-evidence.py \
		--baseline-artifact-dir "$${BASELINE_ARTIFACT_DIR:?set BASELINE_ARTIFACT_DIR to the locked baseline artifact directory}" \
		--candidate-artifact-dir "$${CANDIDATE_ARTIFACT_DIR:?set CANDIDATE_ARTIFACT_DIR to the locked IFPC candidate artifact directory}" \
		--reproduced-candidate-artifact-dir "$${REPRODUCED_CANDIDATE_ARTIFACT_DIR:?set REPRODUCED_CANDIDATE_ARTIFACT_DIR to the independently reproduced candidate artifacts}" \
		--baseline-root "$${BASELINE_ROOT:?set BASELINE_ROOT to a new extraction of the signed baseline RPM}" \
		--candidate-root "$${CANDIDATE_ROOT:?set CANDIDATE_ROOT to a new extraction of the IFPC candidate RPM}" \
		--reproduced-candidate-root "$${REPRODUCED_CANDIDATE_ROOT:?set REPRODUCED_CANDIDATE_ROOT to a new extraction of the independently reproduced candidate RPM}" \
		--pretty

verify-kernel-rollback-preflight:
	./tools/kernel-ab/rollback/preflight.py \
		--rollback-artifact-dir "$${ROLLBACK_ARTIFACT_DIR:?set ROLLBACK_ARTIFACT_DIR to the locked live rollback artifacts}" \
		--baseline-artifact-dir "$${BASELINE_ARTIFACT_DIR:?set BASELINE_ARTIFACT_DIR to the locked baseline build artifacts}" \
		--reference-root "$${REFERENCE_ROOT:?set REFERENCE_ROOT to a fresh signed baseline RPM extraction}" \
		--rebuilt-root "$${REBUILT_ROOT:?set REBUILT_ROOT to a fresh attested baseline RPM extraction}" \
		--pretty

compose-kernel-runtime-boot:
	./tools/kernel-ab/runtime-boot/compose.py \
		--artifact-dir "$${ARTIFACT_DIR:?set ARTIFACT_DIR to the locked candidate/runtime evidence directory}" \
		--output "$${OUTPUT:?set OUTPUT to a new file with the locked basename}" \
		--pretty

verify-kernel-runtime-boot:
	./tools/kernel-ab/runtime-boot/compose.py \
		--artifact-dir "$${ARTIFACT_DIR:?set ARTIFACT_DIR to the locked candidate/runtime evidence directory}" \
		--verify-existing "$${IMAGE:?set IMAGE to the existing locked runtime boot image}" \
		--pretty

preflight-kernel-recovery:
	./tools/kernel-ab/recovery/preflight.py \
		--artifact-dir "$${ARTIFACT_DIR:?set ARTIFACT_DIR to the private baseline/candidate image directory}" \
		--abl-payload "$${ABL_PAYLOAD:?set ABL_PAYLOAD to the official locked v1.1.8 payload}" \
		--fastboot "$${FASTBOOT:?set FASTBOOT to the real locked platform-tools binary}" \
		--pretty

preflight-kernel-ab-workload:
	./tools/kernel-ab/workload/preflight.py --pretty

preflight-kernel-ab-run:
	./tools/kernel-ab/workload/run.py \
		--variant "$${VARIANT:?set baseline or candidate}" \
		--profile "$${PROFILE:?set a locked display profile}" \
		--round "$${ROUND:?set 1, 2 or 3}" \
		--live-preflight

plan-kernel-ab-run:
	./tools/kernel-ab/workload/run.py \
		--variant "$${VARIANT:?set baseline or candidate}" \
		--profile "$${PROFILE:?set a locked display profile}" \
		--round "$${ROUND:?set 1, 2 or 3}"

run-kernel-ab-cell:
	./tools/kernel-ab/workload/run.py \
		--variant "$${VARIANT:?set baseline or candidate}" \
		--profile "$${PROFILE:?set a locked display profile}" \
		--round "$${ROUND:?set 1, 2 or 3}" \
		--execute --confirm "$${CONFIRM:?set CONFIRM to PDS002-RUN-IFPC-WEBGL-V1}" \
		--output "$${OUTPUT:?set OUTPUT to the exact new private matrix filename}"

plan-kernel-boot-alias:
	./tools/kernel-ab/recovery/transaction.py \
		--baseline-image "$${BASELINE_IMAGE:?set BASELINE_IMAGE to the locked live baseline image}" \
		--candidate-image "$${CANDIDATE_IMAGE:?set CANDIDATE_IMAGE to the locked IFPC runtime image}" \
		--target "$${TARGET:?set TARGET to baseline or candidate}" \
		--pretty

stage-kernel-candidate:
	./tools/kernel-ab/recovery/transaction.py \
		--baseline-image "$${BASELINE_IMAGE:?set BASELINE_IMAGE to the locked live baseline image}" \
		--candidate-image "$${CANDIDATE_IMAGE:?set CANDIDATE_IMAGE to the locked IFPC runtime image}" \
		--target candidate \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to PDS002-STAGE-IFPC-CANDIDATE-V1}" \
		--output "$${OUTPUT:?set OUTPUT to a new private transaction report}"

restore-kernel-baseline:
	./tools/kernel-ab/recovery/transaction.py \
		--baseline-image "$${BASELINE_IMAGE:?set BASELINE_IMAGE to the locked live baseline image}" \
		--candidate-image "$${CANDIDATE_IMAGE:?set CANDIDATE_IMAGE to the locked IFPC runtime image}" \
		--target baseline \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to PDS002-RESTORE-LIVE-BASELINE-V1}" \
		--output "$${OUTPUT:?set OUTPUT to a new private transaction report}"

plan-kernel-recovery:
	./tools/kernel-ab/recovery/dispatch.py \
		--artifact-dir "$${ARTIFACT_DIR:?set ARTIFACT_DIR to the private baseline/candidate image directory}" \
		--abl-payload "$${ABL_PAYLOAD:?set ABL_PAYLOAD to the official locked v1.1.8 payload}" \
		--fastboot "$${FASTBOOT:?set FASTBOOT to the real locked platform-tools binary}" \
		--pretty

dispatch-kernel-recovery:
	./tools/kernel-ab/recovery/dispatch.py \
		--artifact-dir "$${ARTIFACT_DIR:?set ARTIFACT_DIR to the private baseline/candidate image directory}" \
		--abl-payload "$${ABL_PAYLOAD:?set ABL_PAYLOAD to the official locked v1.1.8 payload}" \
		--fastboot "$${FASTBOOT:?set FASTBOOT to the real locked platform-tools binary}" \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to PDS002-TEMP-BOOT-BASELINE-V1}" \
		--output "$${OUTPUT:?set OUTPUT to a new private dispatch report}"

attest-kernel-recovery:
	./tools/kernel-ab/recovery/runtime-attest.py \
		--running "$${RUNNING:?set RUNNING to baseline or candidate}" \
		--disk "$${DISK:?set DISK to baseline or candidate}" \
		--max-uptime-seconds "$${MAX_UPTIME_SECONDS:-900}" \
		--pretty

evaluate-kernel-recovery:
	./tools/kernel-ab/recovery/evaluate.py \
		--static-preflight "$${STATIC_PREFLIGHT:?set STATIC_PREFLIGHT to the private static rollback report}" \
		--host-preflight "$${HOST_PREFLIGHT:?set HOST_PREFLIGHT to the private host preflight report}" \
		--dispatch "$${DISPATCH_REPORT:?set DISPATCH_REPORT to the private dispatch report}" \
		--ram-attestation "$${RAM_ATTESTATION:?set RAM_ATTESTATION to the private baseline-over-candidate report}" \
		--restored-attestation "$${RESTORED_ATTESTATION:?set RESTORED_ATTESTATION to the private same-boot restored report}" \
		--normal-boot-attestation "$${NORMAL_ATTESTATION:?set NORMAL_ATTESTATION to the private new normal-boot report}" \
		--output "$${OUTPUT:?set OUTPUT to a new private validation report}"

evaluate-kernel-ab:
	./tools/kernel-ab/ab-evaluate.py \
		--ledger "$${LEDGER:?set LEDGER to the private matched A/B ledger}" \
		--output "$${OUTPUT:?set OUTPUT to a new private A/B evaluation report}" \
		$${REQUIRE_PASS:+--require-pass}

prepare-kernel-ab-ledger:
	./tools/kernel-ab/ab-ledger.py \
		--workload-dual-165-60 "$${WORKLOAD_DUAL_165_60:?set the frozen workload SHA-256}" \
		--workload-dual-60-60 "$${WORKLOAD_DUAL_60_60:?set the frozen workload SHA-256}" \
		--workload-upper-only-165 "$${WORKLOAD_UPPER_ONLY_165:?set the frozen workload SHA-256}" \
		--output "$${OUTPUT:?set OUTPUT to a new private ledger path}"

observe-telemetry-soak:
	./scripts/pds008-telemetry-soak.py --duration $${DURATION:-86400} --interval $${INTERVAL:-2} --output "$${OUTPUT:?set OUTPUT to a new report path}" --require-pass

observe-phone-integration:
	./scripts/pds013-phone-integration-status.py --output "$${OUTPUT:--}" --require-pass

observe-emulation-log:
	./scripts/pds012-esde-log-summary.py --output "$${OUTPUT:--}"

observe-deep-preflight:
	./scripts/pds001-deep-cycle.py

evaluate-deep-series:
	./scripts/pds001-deep-series.py \
		--reports \
		"$${REPORT1:?set REPORT1 to the discharging short report}" \
		"$${REPORT2:?set REPORT2 to the charging short report}" \
		"$${REPORT3:?set REPORT3 to the full short report}" \
		"$${REPORT4:?set REPORT4 to the discharging five-minute report}" \
		--output "$${OUTPUT:?set OUTPUT to a new private series report}"

evaluate-asr:
	./scripts/pds011-asr-evaluate.py \
		--corpus "$${CORPUS:?set CORPUS to a private mode-0600 corpus manifest}" \
		--results "$${RESULTS:?set RESULTS to one private mode-0600 backend result}" \
		--output "$${OUTPUT:--}" \
		--max-micro-cer "$${MAX_MICRO_CER:?set an explicit CER limit}" \
		--max-p95-latency-ms "$${MAX_P95_LATENCY_MS:?set an explicit latency limit}" \
		--max-peak-rss-bytes "$${MAX_PEAK_RSS_BYTES:?set an explicit memory limit}"

evaluate-audio-matrix:
	./scripts/pds018-audio-matrix-evaluate.py \
		--evidence "$${EVIDENCE:?set EVIDENCE to a private supervised audio ledger}" \
		--repo-revision "$${REPO_REVISION:-$$(git rev-parse HEAD)}" \
		--output "$${OUTPUT:--}"

prepare-battery-ledger:
	./scripts/pds006-battery-matrix-evaluate.py \
		--template-output "$${LEDGER:?set LEDGER to a new private evidence path}" \
		--repo-revision "$${REPO_REVISION:-$$(git rev-parse HEAD)}"

evaluate-battery-matrix:
	./scripts/pds006-battery-matrix-evaluate.py \
		--evidence "$${EVIDENCE:?set EVIDENCE to the completed private battery ledger}" \
		--repo-revision "$${REPO_REVISION:-$$(git rev-parse HEAD)}" \
		--output "$${OUTPUT:--}"

prepare-usb-wake-ledger:
	./scripts/pds023-usb-wake-evaluate.py \
		--template-output "$${LEDGER:?set LEDGER to a new private evidence path}" \
		--repo-revision "$${REPO_REVISION:-$$(git rev-parse HEAD)}"

evaluate-usb-wake-matrix:
	./scripts/pds023-usb-wake-evaluate.py \
		--evidence "$${EVIDENCE:?set EVIDENCE to the completed private USB wake ledger}" \
		--repo-revision "$${REPO_REVISION:-$$(git rev-parse HEAD)}" \
		--output "$${OUTPUT:--}"

prepare-ui-input-ledger:
	./scripts/pds005-ui-input-evaluate.py \
		--transaction-name "$${TRANSACTION:?set the applied UI transaction name}" \
		--template-output "$${LEDGER:?set LEDGER to a new private evidence path}"

evaluate-ui-input:
	./scripts/pds005-ui-input-evaluate.py \
		--transaction-name "$${TRANSACTION:?set the applied UI transaction name}" \
		--evidence "$${EVIDENCE:?set EVIDENCE to the completed private UI/input ledger}" \
		--output "$${OUTPUT:--}"

plan-xwayland-isolation:
	./scripts/pds004-xwayland-isolation.py

accept-xwayland-isolation:
	./scripts/pds004-xwayland-isolation.py \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-RUN-NESTED-XWAYLAND-FAILURE}" \
		--rounds "$${ROUNDS:-3}" \
		--output "$${OUTPUT:?set OUTPUT to a new private report path}"

audit-ui-deployment:
	./scripts/pds005-ui-deployment-audit.py --output "$${OUTPUT:--}"

audit-ui-deployment-candidate:
	./scripts/pds005-ui-deployment-audit.py \
		--panelctl-candidate "$${PANELCTL_CANDIDATE:?set a current-source panelctl candidate}" \
		--output "$${OUTPUT:--}"

plan-ui-update:
	./scripts/pds005-ui-update-transaction.py

stage-ui-update:
	./scripts/pds005-ui-update-transaction.py \
		--stage "$${TRANSACTION:?set a new transaction name}" \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-STAGE-TEN-UI-FILES}"

apply-ui-update:
	./scripts/pds005-ui-update-transaction.py \
		--apply "$${TRANSACTION:?set the staged transaction name}" \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-APPLY-TEN-UI-FILES}"

verify-ui-update:
	./scripts/pds005-ui-update-transaction.py \
		--verify "$${TRANSACTION:?set the applied transaction name}"

rollback-ui-update:
	./scripts/pds005-ui-update-transaction.py \
		--rollback "$${TRANSACTION:?set the applied transaction name}" \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-ROLLBACK-TEN-UI-FILES}"

post-boot-plan:
	./scripts/pds014-post-boot-acceptance.py

accept-post-boot:
	./scripts/pds014-post-boot-acceptance.py \
		--execute \
		--confirm "$${CONFIRM:?set CONFIRM to POCKETDS-POST-BOOT-READONLY}" \
		--output "$${OUTPUT:?set OUTPUT to a new private report path}" \
		--max-start-uptime-seconds "$${MAX_START_UPTIME_SECONDS:-900}"

evaluate-post-boot-series:
	./scripts/pds014-post-boot-series.py \
		--reports \
		"$${REPORT1:?set REPORT1 to the first private post-boot report}" \
		"$${REPORT2:?set REPORT2 to the second private post-boot report}" \
		"$${REPORT3:?set REPORT3 to the third private post-boot report}" \
		"$${REPORT4:?set REPORT4 to the fourth private post-boot report}" \
		"$${REPORT5:?set REPORT5 to the fifth private post-boot report}" \
		--output "$${OUTPUT:?set OUTPUT to a new private series report}"

diagnostic-bundle:
	./scripts/diagnostic-bundle.sh

install:
	./scripts/install.sh --apps

install-all:
	./scripts/install.sh --all

install-emulation:
	./scripts/install-emulation.sh

install-game-input-stack:
	python3 ./scripts/install-game-input-stack.py

install-brightness:
	./scripts/install-brightness.sh

install-codex-quota:
	./scripts/install-codex-quota.sh

install-telemetry:
	./scripts/install-telemetry.sh

install-moonlight-fps:
	./components/moonlight/pocketds-moonlight-fps-setup install

install-chinese-input:
	./scripts/install-chinese-input.sh --apply \
		--confirm POCKETDS-INSTALL-ZH-CN-RIME-ICE

import-live:
	./scripts/import-live.sh --yes

status:
	git status --short --branch

test-source-preview:
	python3 ./tests/source-preview.py

export-source-preview:
	python3 ./scripts/export-source-preview.py --repo . --output "$${OUTPUT:?set OUTPUT to a new directory outside the repository}"
