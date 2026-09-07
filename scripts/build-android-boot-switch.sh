#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app_root="$repo_root/components/android-boot-switch"
output_root="${1:-$repo_root/build/android-boot-switch}"

: "${ANDROID_SDK_ROOT:?Set ANDROID_SDK_ROOT to an Android SDK containing build-tools 35.0.0 and platform 33}"
: "${JAVA_HOME:?Set JAVA_HOME to a JDK 21 installation}"
: "${POCKETDS_ANDROID_KEYSTORE:?Set POCKETDS_ANDROID_KEYSTORE to the archived dual-boot signing keystore}"
: "${POCKETDS_ANDROID_KEYSTORE_PASSWORD:?Set POCKETDS_ANDROID_KEYSTORE_PASSWORD}"

build_tools="$ANDROID_SDK_ROOT/build-tools/35.0.0"
android_jar="$ANDROID_SDK_ROOT/platforms/android-33/android.jar"
for required in aapt2 d8 zipalign apksigner; do
    test -x "$build_tools/$required" || {
        echo "missing Android build tool: $build_tools/$required" >&2
        exit 1
    }
done
test -f "$android_jar"
test -x "$JAVA_HOME/bin/javac"
test -f "$POCKETDS_ANDROID_KEYSTORE"

work_root="$(mktemp -d "${TMPDIR:-/tmp}/pocketds-android-boot-switch.XXXXXX")"
trap 'rm -rf "$work_root"' EXIT HUP INT TERM
mkdir -p "$work_root/classes" "$work_root/dex" "$output_root"

"$build_tools/aapt2" compile --dir "$app_root/res" -o "$work_root/resources.zip"
"$build_tools/aapt2" link \
    -o "$work_root/base-unsigned.apk" \
    -I "$android_jar" \
    --manifest "$app_root/AndroidManifest.xml" \
    --min-sdk-version 26 \
    --target-sdk-version 33 \
    --version-code 2 \
    --version-name 2.0 \
    "$work_root/resources.zip"

"$JAVA_HOME/bin/javac" \
    -encoding UTF-8 \
    -source 8 \
    -target 8 \
    -classpath "$android_jar" \
    -d "$work_root/classes" \
    "$app_root/src/li/azka/pocketds/dualboot/MainActivity.java"

JAVA_HOME="$JAVA_HOME" "$build_tools/d8" \
    --min-api 26 \
    --lib "$android_jar" \
    --output "$work_root/dex" \
    "$work_root/classes/li/azka/pocketds/dualboot/"*.class

cp "$work_root/base-unsigned.apk" "$work_root/with-dex.apk"
(cd "$work_root/dex" && zip -q -j "$work_root/with-dex.apk" classes.dex)
"$build_tools/zipalign" -f -p 4 "$work_root/with-dex.apk" "$work_root/aligned.apk"

apk="$output_root/PocketDS-切换到Linux.apk"
JAVA_HOME="$JAVA_HOME" "$build_tools/apksigner" sign \
    --ks "$POCKETDS_ANDROID_KEYSTORE" \
    --ks-key-alias "${POCKETDS_ANDROID_KEY_ALIAS:-pocketds-dualboot}" \
    --ks-pass env:POCKETDS_ANDROID_KEYSTORE_PASSWORD \
    --key-pass env:POCKETDS_ANDROID_KEYSTORE_PASSWORD \
    --out "$apk" \
    "$work_root/aligned.apk"
JAVA_HOME="$JAVA_HOME" "$build_tools/apksigner" verify --verbose --print-certs "$apk"
"$build_tools/aapt2" dump badging "$apk" | head -n 12
shasum -a 256 "$apk"
