#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/rtwo/pins.env"

variant=${VARIANT:-sukisu-kpm}
variant_file="$project_root/config/rtwo/variants/$variant.env"
[[ -f "$variant_file" ]] || { echo "unknown rtwo variant: $variant" >&2; exit 1; }
# shellcheck disable=SC1090
source "$variant_file"
"$project_root/scripts/verify-rtwo-pins.sh"

if [[ "$(uname -s)" != Linux ]]; then
  echo "The rtwo kernel build must run on Linux; use the GitHub Actions workflow from macOS." >&2
  exit 2
fi
[[ "$(uname -m)" == x86_64 ]] || {
  echo "The checked-in Android 13/5.15 build is intended for Linux x86-64." >&2
  exit 2
}

RTWO_WORKDIR=${RTWO_WORKDIR:-$project_root/.work/rtwo-android13-5.15/$variant}
export RTWO_WORKDIR
VARIANT="$variant" "$project_root/scripts/prepare-rtwo-source.sh"

source_dir=$RTWO_WORKDIR/source
out_dir=$RTWO_WORKDIR/out
mkdir -p "$out_dir"
build_jobs=${KERNEL_BUILD_JOBS:-$(nproc)}
[[ "$build_jobs" =~ ^[1-9][0-9]*$ ]] || {
  echo "KERNEL_BUILD_JOBS must be a positive integer" >&2
  exit 1
}
build_epoch=$(git -C "$source_dir" show -s --format=%ct HEAD)
export SOURCE_DATE_EPOCH=$build_epoch
export KBUILD_BUILD_TIMESTAMP
KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$build_epoch" '+%a %b %d %H:%M:%S UTC %Y')

make_args=(
  -C "$source_dir" O="$out_dir" ARCH=arm64 LLVM=1 LLVM_IAS=1
)

make "${make_args[@]}" gki_defconfig
KCONFIG_CONFIG="$out_dir/.config" "$source_dir/scripts/kconfig/merge_config.sh" \
  -m -r "$out_dir/.config" \
  "$source_dir/arch/arm64/configs/vendor/kalama_GKI.config" \
  "$source_dir/arch/arm64/configs/vendor/ext_config/moto-kalama.config" \
  "$source_dir/arch/arm64/configs/vendor/ext_config/moto-kalama-gki.config" \
  "$source_dir/arch/arm64/configs/vendor/ext_config/moto-kalama-rtwo.config" \
  "$RTWO_WORKDIR/root.fragment"
make "${make_args[@]}" olddefconfig

# Build only the common GKI Image. The phone's matching vendor/system_dlkm
# modules remain untouched and are not replaced by this project.
make "${make_args[@]}" -j"$build_jobs" Image

image="$out_dir/arch/arm64/boot/Image"
[[ -f "$image" ]] || { echo "kernel build produced no Image" >&2; exit 1; }

artifact_dir=$project_root/out/rtwo/$variant
mkdir -p "$artifact_dir"
cp "$image" "$artifact_dir/Image"
sha256sum "$artifact_dir/Image" > "$artifact_dir/Image.sha256"
{
  echo "variant=$variant"
  echo "device=$RTWO_DEVICE"
  echo "platform=$RTWO_PLATFORM"
  echo "android_kernel_family=$ANDROID_KERNEL_FAMILY"
  echo "lineage_fingerprint=$LINEAGE_FINGERPRINT"
  echo "lineage_security_patch=$LINEAGE_SECURITY_PATCH"
  echo "lineage_kernel_release=$LINEAGE_KERNEL_RELEASE"
  echo "kernel=$(git -C "$source_dir" rev-parse HEAD)"
  echo "modules=$(git -C "$source_dir/../sm8550-modules" rev-parse HEAD)"
  echo "devicetrees=$(git -C "$source_dir/../sm8550-devicetrees" rev-parse HEAD)"
  echo "root=$(grep '^root=' "$RTWO_WORKDIR/source-pins.txt" | cut -d= -f2-)"
  echo "build_jobs=$build_jobs"
  if [[ "$ENABLE_SUSFS" == 1 ]]; then
    echo "susfs=$(grep '^susfs=' "$RTWO_WORKDIR/source-pins.txt" | cut -d= -f2-)"
  fi
  echo "source_date_epoch=$SOURCE_DATE_EPOCH"
  echo "config_sha256=$(sha256sum "$out_dir/.config" | awk '{print $1}')"
} > "$artifact_dir/build-metadata.txt"
echo "built $artifact_dir/Image"
