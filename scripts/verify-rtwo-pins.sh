#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
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
[[ "$LINEAGE_BOOT_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "invalid LINEAGE_BOOT_SHA256" >&2
  exit 1
}
[[ "$LINEAGE_BOOT_URL" == https://mirrorbits.lineageos.org/full/rtwo/*/boot.img ]] || {
  echo "unexpected LINEAGE_BOOT_URL=$LINEAGE_BOOT_URL" >&2
  exit 1
}

[[ "$RTWO_DEVICE" == rtwo ]] || { echo "unexpected RTWO_DEVICE" >&2; exit 1; }
[[ "$RTWO_PLATFORM" == kalama ]] || { echo "unexpected RTWO_PLATFORM" >&2; exit 1; }
[[ "$ANDROID_KERNEL_FAMILY" == android13-5.15 ]] || {
  echo "unexpected Android kernel family" >&2
  exit 1
}
[[ "$LINEAGE_KERNEL_RELEASE" == 5.15.208-ge3f43b79f663 ]] || {
  echo "Lineage kernel release no longer matches the recorded device" >&2
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
