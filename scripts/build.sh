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
while IFS= read -r candidate; do
  image_file=$candidate
  break
done < <(find bazel-bin bazel-out out -type f -name Image -print 2>/dev/null | sort)
[[ -n "$image_file" ]] || {
  echo "Kleaf completed but no Image was found; inspect the bazel output under $source_dir" >&2
  exit 1
}

output_dir=$project_root/out/$variant
mkdir -p "$output_dir"
cp "$image_file" "$output_dir/Image"
sha256sum "$output_dir/Image" > "$output_dir/Image.sha256"
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
} > "$output_dir/build-metadata.txt"
echo "built $output_dir/Image"
