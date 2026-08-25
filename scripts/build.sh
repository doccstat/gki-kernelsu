#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
variant=${VARIANT:-sukisu-kpm}
# shellcheck disable=SC1091
source "$project_root/config/pins.env"

"$project_root/scripts/verify-pins.sh"

if [[ "$(uname -s)" != Linux ]]; then
  echo "The Google Kleaf build must run on Linux x86-64; use GitHub Actions from macOS." >&2
  exit 2
fi
[[ "$(uname -m)" == x86_64 ]] || {
  echo "The checked-in Google toolchain is linux-x86; use an x86-64 Linux runner." >&2
  exit 2
}

YOGI_WORKDIR=${YOGI_WORKDIR:-$project_root/.work/gs101-android16-6.12}
export YOGI_WORKDIR
VARIANT="$variant" "$project_root/scripts/sync-source.sh"
VARIANT="$variant" "$project_root/scripts/prepare-source.sh"

source_dir=$YOGI_WORKDIR/source
build_epoch=$(git -C "$source_dir/common" show -s --format=%ct HEAD)
export SOURCE_DATE_EPOCH=$build_epoch
export KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$build_epoch" '+%a %b %d %H:%M:%S UTC %Y')

cd "$source_dir"
./build_raviole.sh

image_file=
for candidate in \
  "$source_dir/out/slider/dist/Image" \
  "$source_dir/bazel-bin/private/google-modules/soc/gs/slider_dist/Image" \
  "$source_dir/bazel-bin/private/google-modules/soc/gs/Image"; do
  if [[ -f "$candidate" ]]; then
    image_file=$candidate
    break
  fi
done
if [[ -z "$image_file" ]]; then
  while IFS= read -r candidate; do
    image_file=$candidate
    break
  done < <(find bazel-bin bazel-out out -type f -name Image -print 2>/dev/null | sort)
fi
[[ -n "$image_file" ]] || {
  echo "Kleaf completed but no Image was found; inspect the bazel output under $source_dir" >&2
  exit 1
}

final_config_file=$YOGI_WORKDIR/final-kernel.config
python3 "$project_root/scripts/extract-kernel-config.py" "$image_file" > "$final_config_file"
VARIANT="$variant" "$project_root/scripts/validate-kernel-config.sh" \
  "$final_config_file" "$variant"

boot_image_file=
for candidate in \
  "$source_dir/out/slider/dist/boot.img" \
  "$source_dir/bazel-bin/private/google-modules/soc/gs/slider_dist/boot.img" \
  "$source_dir/bazel-bin/private/google-modules/soc/gs/boot.img"; do
  if [[ -f "$candidate" ]]; then
    boot_image_file=$candidate
    break
  fi
done
if [[ -z "$boot_image_file" ]]; then
  while IFS= read -r candidate; do
    if python3 - "$candidate" <<'PY'
import sys
from pathlib import Path

with Path(sys.argv[1]).open("rb") as stream:
    raise SystemExit(0 if stream.read(8) == b"ANDROID!" else 1)
PY
    then
      boot_image_file=$candidate
      break
    fi
  done < <(find bazel-bin bazel-out out -type f -name boot.img -print 2>/dev/null | sort)
fi
[[ -n "$boot_image_file" ]] || {
  echo "Kleaf completed but no Android boot.img was found; inspect the bazel output under $source_dir" >&2
  exit 1
}
boot_header_version=$(python3 - "$boot_image_file" <<'PY'
import struct
import sys
from pathlib import Path

with Path(sys.argv[1]).open("rb") as stream:
    header = stream.read(44)
if len(header) < 44 or header[:8] != b"ANDROID!":
    raise SystemExit("boot artifact has no Android boot header")
print(struct.unpack_from("<I", header, 40)[0])
PY
)
[[ "$boot_header_version" == 3 || "$boot_header_version" == 4 ]] || {
  echo "unsupported Android boot header version: $boot_header_version" >&2
  exit 1
}

output_dir=$project_root/out/$variant
mkdir -p "$output_dir"
cp "$image_file" "$output_dir/Image"
sha256sum "$output_dir/Image" > "$output_dir/Image.sha256"
cp "$final_config_file" "$output_dir/kernel.config"
sha256sum "$output_dir/kernel.config" > "$output_dir/kernel.config.sha256"
cp "$boot_image_file" "$output_dir/boot.img"
sha256sum "$output_dir/boot.img" > "$output_dir/boot.img.sha256"
{
  echo "variant=$variant"
  echo "device=yogi"
  echo "platform=gs101"
  echo "factory_fingerprint=$FACTORY_FINGERPRINT"
  echo "factory_security_patch=$FACTORY_SECURITY_PATCH"
  echo "factory_kernel_release=$FACTORY_KERNEL_RELEASE"
  echo "common_kernel=$(git -C common rev-parse HEAD)"
  echo "common_kernel_version=6.12.89"
  echo "root=$(cat "$YOGI_WORKDIR/selected-variant")"
  echo "source_dir=$source_dir"
  echo "source_date_epoch=$SOURCE_DATE_EPOCH"
  echo "kernel_config_sha256=$(awk '{print $1}' "$output_dir/kernel.config.sha256")"
  echo "boot_source=$boot_image_file"
} > "$output_dir/build-metadata.txt"
{
  echo "variant=$variant"
  echo "device=yogi"
  echo "factory_fingerprint=$FACTORY_FINGERPRINT"
  echo "factory_security_patch=$FACTORY_SECURITY_PATCH"
  echo "factory_kernel_release=$FACTORY_KERNEL_RELEASE"
  echo "source_boot_artifact=Google Kleaf gki_aarch64_boot"
  echo "boot_header_version=$boot_header_version"
  echo "boot_sha256=$(awk '{print $1}' "$output_dir/boot.img.sha256")"
  echo "image_sha256=$(awk '{print $1}' "$output_dir/Image.sha256")"
  echo "kernel_config_sha256=$(awk '{print $1}' "$output_dir/kernel.config.sha256")"
  echo "boot_size_bytes=$(wc -c < "$output_dir/boot.img" | tr -d ' ')"
} > "$output_dir/boot-metadata.txt"
echo "built $output_dir/Image and $output_dir/boot.img"
