#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/rtwo/pins.env"

variant=${VARIANT:-sukisu-kpm}
variant_file="$project_root/config/rtwo/variants/$variant.env"
[[ -f "$variant_file" ]] || {
  echo "unknown rtwo variant: $variant" >&2
  exit 1
}

"$project_root/scripts/verify-rtwo-pins.sh" >/dev/null

artifact_dir=${RTWO_ARTIFACT_DIR:-$project_root/out/rtwo/$variant}
image=${RTWO_IMAGE:-$artifact_dir/Image}
base_boot=${RTWO_BASE_BOOT_IMAGE:-$project_root/.work/rtwo-base/$LINEAGE_BUILD_DATE/boot.img}
base_url=${RTWO_BASE_BOOT_URL:-$LINEAGE_BOOT_URL}
base_sha256=${RTWO_BASE_BOOT_SHA256:-$LINEAGE_BOOT_SHA256}
tools_dir=${MKBOOTIMG_DIR:-$project_root/.work/mkbootimg}

[[ -f "$image" ]] || {
  echo "missing raw kernel Image: $image" >&2
  exit 1
}
[[ "$base_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "invalid base boot SHA-256" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || {
  echo "curl is required to fetch the pinned base boot image" >&2
  exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
  echo "sha256sum is required to verify the base boot image" >&2
  exit 1
}

"$project_root/scripts/fetch-mkbootimg-tools.sh"
[[ -f "$tools_dir/mkbootimg.py" && -f "$tools_dir/unpack_bootimg.py" ]] || {
  echo "mkbootimg tools are incomplete in $tools_dir" >&2
  exit 1
}

mkdir -p "$(dirname "$base_boot")" "$artifact_dir" "$project_root/.work"
if [[ ! -f "$base_boot" ]]; then
  curl --fail --location --retry 3 --retry-delay 2 --retry-all-errors \
    --output "$base_boot" "$base_url"
fi
printf '%s  %s\n' "$base_sha256" "$base_boot" | sha256sum --check --status - || {
  echo "base boot image checksum mismatch: $base_boot" >&2
  exit 1
}

package_work=$(mktemp -d "$project_root/.work/package-rtwo-boot.XXXXXX")
trap 'rm -rf -- "$package_work"' EXIT
unpacked_dir="$package_work/unpacked"
mkdir -p "$unpacked_dir"

python3 "$tools_dir/unpack_bootimg.py" \
  --boot_img "$base_boot" \
  --out "$unpacked_dir" \
  --format info | tee "$package_work/base-boot-info.txt"
grep -q '^boot image header version: 4$' "$package_work/base-boot-info.txt" || {
  echo "the pinned rtwo base boot image is not a v4 boot image" >&2
  exit 1
}

declare -a mkbootimg_args=()
while IFS= read -r -d '' arg; do
  mkbootimg_args+=("$arg")
done < <(
  python3 "$tools_dir/unpack_bootimg.py" \
    --boot_img "$base_boot" \
    --out "$unpacked_dir" \
    --format mkbootimg \
    --null
)

kernel_arg=-1
for ((index = 0; index < ${#mkbootimg_args[@]}; index++)); do
  if [[ "${mkbootimg_args[index]}" == --kernel ]]; then
    kernel_arg=$((index + 1))
    break
  fi
done
[[ "$kernel_arg" -ge 0 ]] || {
  echo "unpack_bootimg did not emit a kernel argument" >&2
  exit 1
}
mkbootimg_args[kernel_arg]="$image"

boot_image="$artifact_dir/boot.img"
python3 "$tools_dir/mkbootimg.py" \
  "${mkbootimg_args[@]}" \
  --output "$boot_image"

# Match the published boot partition image size. The generated image is
# otherwise byte-for-byte the AOSP v4 layout described by the base image; the
# trailing bytes are only partition padding.
python3 - "$boot_image" "$base_boot" <<'PY'
from pathlib import Path
import sys

output = Path(sys.argv[1])
base = Path(sys.argv[2])
output_size = output.stat().st_size
base_size = base.stat().st_size
if output_size > base_size:
    raise SystemExit(
        f"repacked boot image is larger than its base ({output_size} > {base_size})"
    )
with output.open("r+b") as stream:
    stream.truncate(base_size)
PY

verify_dir="$package_work/verify"
python3 "$tools_dir/unpack_bootimg.py" \
  --boot_img "$boot_image" \
  --out "$verify_dir" \
  --format info > "$package_work/repacked-boot-info.txt"
grep -q '^boot image header version: 4$' "$package_work/repacked-boot-info.txt" || {
  echo "repacked boot image does not have a v4 header" >&2
  exit 1
}
cmp -s "$image" "$verify_dir/kernel" || {
  echo "repacked boot image kernel does not match the built Image" >&2
  exit 1
}

sha256sum "$boot_image" > "$artifact_dir/boot.img.sha256"
{
  echo "variant=$variant"
  echo "device=$RTWO_DEVICE"
  echo "lineage_fingerprint=$LINEAGE_FINGERPRINT"
  echo "lineage_build_version=$LINEAGE_BUILD_VERSION"
  echo "lineage_build_date=$LINEAGE_BUILD_DATE"
  echo "lineage_kernel_release=$LINEAGE_KERNEL_RELEASE"
  echo "base_boot_url=$base_url"
  echo "base_boot_sha256=$base_sha256"
  echo "mkbootimg_repo=$MKBOOTIMG_REPO"
  echo "mkbootimg_ref=$MKBOOTIMG_REF"
  echo "image_sha256=$(sha256sum "$image" | awk '{print $1}')"
  echo "boot_sha256=$(awk '{print $1}' "$artifact_dir/boot.img.sha256")"
  echo "boot_size_bytes=$(wc -c < "$boot_image" | tr -d ' ')"
} > "$artifact_dir/boot-metadata.txt"

echo "packaged $boot_image"
