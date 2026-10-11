#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/rtwo/pins.env"

is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }

for name in KERNEL_REF MODULES_REF DEVICETREES_REF LINEAGE_DEVICE_REF \
  LINEAGE_COMMON_REF SUKISU_REF RESUKISU_REF SUSFS_REF MKBOOTIMG_REF; do
  is_sha "${!name}" || { echo "invalid $name=${!name}" >&2; exit 1; }
done

[[ "$LINEAGE_BUILD_DATE" =~ ^[0-9]{8}$ ]] || {
  echo "invalid LINEAGE_BUILD_DATE=$LINEAGE_BUILD_DATE" >&2
  exit 1
}
[[ "$LINEAGE_BUILD_VERSION" =~ ^[0-9]+\.[0-9]+$ ]] || {
  echo "invalid LINEAGE_BUILD_VERSION=$LINEAGE_BUILD_VERSION" >&2
  exit 1
}
for name in LINEAGE_BOOT_SHA256 LINEAGE_MANIFEST_SHA256; do
  [[ "${!name}" =~ ^[0-9a-f]{64}$ ]] || {
    echo "invalid $name" >&2
    exit 1
  }
done
[[ "$LINEAGE_BOOT_URL" == "https://mirrorbits.lineageos.org/full/rtwo/$LINEAGE_BUILD_DATE/boot.img" ]] || {
  echo "LINEAGE_BOOT_URL does not match LINEAGE_BUILD_DATE" >&2
  exit 1
}
[[ "$LINEAGE_MANIFEST_URL" == "https://mirrorbits.lineageos.org/full/rtwo/$LINEAGE_BUILD_DATE/build-manifest.xml" ]] || {
  echo "LINEAGE_MANIFEST_URL does not match LINEAGE_BUILD_DATE" >&2
  exit 1
}

[[ "$RTWO_DEVICE" == rtwo ]] || { echo "unexpected RTWO_DEVICE" >&2; exit 1; }
[[ "$RTWO_PLATFORM" == kalama ]] || { echo "unexpected RTWO_PLATFORM" >&2; exit 1; }
[[ "$ANDROID_KERNEL_FAMILY" == android13-5.15 ]] || {
  echo "unexpected Android kernel family" >&2
  exit 1
}
[[ "$KERNEL_VERSION" =~ ^5\.15\.[0-9]+$ && "$KERNEL_RELEASE" == "$KERNEL_VERSION-android13" ]] || {
  echo "kernel version/release no longer matches the Android 13/5.15 target" >&2
  exit 1
}
[[ "$LINEAGE_KERNEL_RELEASE" == "$KERNEL_VERSION-g${KERNEL_REF:0:12}" ]] || {
  echo "Lineage kernel release no longer matches the pinned source" >&2
  exit 1
}
[[ "$SUSFS_PATCH" == kernel_patches/50_add_susfs_in_gki-android13-5.15.patch ]] || {
  echo "unexpected SUSFS patch path" >&2
  exit 1
}

for variant_file in "$project_root"/config/rtwo/variants/*.env; do
  # shellcheck disable=SC1090
  source "$variant_file"
  [[ "$ENABLE_KPM" == 0 || "$ENABLE_KPM" == 1 ]] || exit 1
  [[ "$ENABLE_SUSFS" == 0 || "$ENABLE_SUSFS" == 1 ]] || exit 1
  if [[ "$ENABLE_KPM" == 1 && "$ENABLE_SUSFS" == 1 ]]; then
    echo "refusing unsupported combined KPM+SUSFS variant: $VARIANT_NAME" >&2
    exit 1
  fi
  echo "validated rtwo variant $VARIANT_NAME"
done

echo "validated rtwo $ANDROID_KERNEL_FAMILY pins for $LINEAGE_KERNEL_RELEASE"
