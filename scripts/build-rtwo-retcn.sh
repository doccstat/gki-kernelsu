#!/usr/bin/env bash
# Independent stock-RETCN build. Never dispatches the LineageOS recipe or flashes a device.
set -euo pipefail
project_root=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/rtwo-retcn/pins.env"
helper=$project_root/scripts/rtwo-retcn.py
python3 "$helper" verify-pins

[[ ${VARIANT:-resukisu-susfs} == resukisu-susfs ]] || {
  echo "RETCN supports only built-in resukisu-susfs; KPM is not integrated" >&2; exit 2;
}
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "RETCN requires Linux x86-64 (pinned clang-r450784e); use GitHub Actions" >&2; exit 2;
}
for tool in git make curl pahole bc bison flex cpio openssl diff; do
  command -v "$tool" >/dev/null || { echo "missing build dependency: $tool" >&2; exit 2; }
done
jobs=${KERNEL_BUILD_JOBS:-2}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo "invalid KERNEL_BUILD_JOBS" >&2; exit 2; }
base_boot=${RTWO_RETCN_BOOT_IMAGE:-}
base_url=${RTWO_RETCN_BOOT_URL:-$STOCK_BOOT_URL}
[[ -n $base_boot || $base_url == https://* ]] || {
  echo "Set RTWO_RETCN_BOOT_IMAGE or an HTTPS RTWO_RETCN_BOOT_URL for the pinned stock boot.img" >&2
  exit 2
}
if [[ -n $base_boot ]]; then
  base_boot=$(realpath -e -- "$base_boot")
fi
artifact_dir=${RTWO_RETCN_ARTIFACT_DIR:-$project_root/out/rtwo-retcn/resukisu-susfs}
[[ ! -e $artifact_dir && ! -L $artifact_dir ]] || {
  echo "artifact directory already exists; select a new RTWO_RETCN_ARTIFACT_DIR" >&2; exit 2;
}
mkdir -p "$project_root/.work"
# Source checkouts are disposable but never reset/clean an existing caller-owned directory.
# Keep failed builds for diagnosis; reruns use a fresh directory, not a partly patched tree.
if [[ -n ${RTWO_RETCN_WORKDIR:-} ]]; then
  mkdir -- "$RTWO_RETCN_WORKDIR"
  work=$(realpath -- "$RTWO_RETCN_WORKDIR")
else
  work=$(mktemp -d "$project_root/.work/rtwo-retcn.XXXXXX")
fi
printf 'RETCN work directory: %s\n' "$work"
free_kb=$(df -Pk "$work" | awk 'END {print $4}')
(( free_kb >= 35 * 1024 * 1024 )) || {
  echo "RETCN needs at least 35 GiB free (100 GiB recommended); no source sync started" >&2; exit 2;
}
if [[ -z $base_boot ]]; then
  base_boot=$work/stock-boot.img
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --retry 3 --connect-timeout 30 --max-time 1200 --max-filesize "$STOCK_BOOT_SIZE" \
    --output "$base_boot" "$base_url"
fi
# Verify firmware before fetching any large source/toolchain trees. No checksum override.
python3 "$helper" extract "$base_boot" "$work/stock"

clone_pin() {
  local url=$1 ref=$2 dest=$3 mode=${4:-shallow}
  git init --quiet "$dest"
  git -C "$dest" remote add origin "$url"
  local args=(--no-tags --filter=blob:none)
  [[ $mode == full ]] || args+=(--depth=1)
  git -C "$dest" fetch "${args[@]}" origin "$ref"
  if (( $# > 4 )); then
    git -C "$dest" sparse-checkout init --cone
    git -C "$dest" sparse-checkout set "${@:5}"
  fi
  git -C "$dest" -c advice.detachedHead=false checkout --detach "$ref"
  [[ $(git -C "$dest" rev-parse HEAD) == "$ref" ]]
}
source_dir=$work/common
root_dir=$work/ReSukiSU
susfs_dir=$work/susfs4ksu
clone_pin "$KERNEL_REPO" "$KERNEL_REF" "$source_dir"
# Kbuild derives its version code from reachable history. Fetch that history now,
# avoiding upstream's implicit --unshallow/network access inside make.
clone_pin "$RESUKISU_REPO" "$RESUKISU_REF" "$root_dir" full
clone_pin "$SUSFS_REPO" "$SUSFS_REF" "$susfs_dir"
clone_pin "$CLANG_REPO" "$CLANG_REF" "$work/clang" shallow "$CLANG_DIR"
clone_pin "$NDK_REPO" "$NDK_REF" "$work/ndk" shallow \
  "$NDK_SYSROOT/usr/include" "$NDK_SYSROOT/usr/lib/aarch64-linux-android"
export PATH="$work/clang/$CLANG_DIR/bin:$PATH"
clang --version | tee "$work/compiler.txt"
grep -Fq "clang version $CLANG_VERSION" "$work/compiler.txt"
grep -Fq "based on ${CLANG_DIR#clang-}" "$work/compiler.txt"
grep -qx 'KMI_GENERATION=8' "$source_dir/build.config.common"
grep -qx 'CLANG_VERSION=r450784e' "$source_dir/build.config.constants"
grep -qx "NDK_TRIPLE=$NDK_TRIPLE" "$source_dir/build.config.aarch64"
grep -Fqx "Pkg.Revision = $NDK_VERSION" "$work/ndk/source.properties"

# Mirror kernel/build's NDK_TRIPLE environment for userspace probes/header tests.
# These are USER flags only: kernel target flags, compiler, LTO and CFI stay stock.
# Do not whitelist failed CC_CAN_LINK probes or disable UAPI_HEADER_TEST instead.
sysroot=$work/ndk/$NDK_SYSROOT
user_cflags=("--target=$NDK_TRIPLE" "--sysroot=$sysroot" -Wno-unused-function)
user_ldflags=(-fuse-ld=lld --rtlib=compiler-rt "--target=$NDK_TRIPLE")
export USERCFLAGS="${user_cflags[*]}" USERLDFLAGS="${user_ldflags[*]}"
{
  printf 'ndk_ref=%s\nUSERCFLAGS=%s\nUSERLDFLAGS=%s\n' "$NDK_REF" "$USERCFLAGS" "$USERLDFLAGS"
  for mode in dynamic static; do
    probe_flags=()
    [[ $mode == dynamic ]] || probe_flags=(-static)
    if ! "$source_dir/scripts/cc-can-link.sh" clang \
      "${user_cflags[@]}" "${user_ldflags[@]}" "${probe_flags[@]}"; then
      echo "RETCN $mode userspace link probe failed; check the pinned NDK sysroot/compiler before configuring" >&2
      exit 1
    fi
    echo "RETCN $mode userspace link probe passed"
  done
} 2>&1 | tee "$work/userspace-toolchain.log"

# The exact 5.15.189 source accepts this pin without Lineage-specific normalization.
# git apply checks full context; do not fuzz, skip failed hunks, or silently continue.
git -C "$source_dir" apply --check "$susfs_dir/$SUSFS_PATCH"
git -C "$source_dir" apply "$susfs_dir/$SUSFS_PATCH"
cp "$susfs_dir/kernel_patches/fs/"* "$source_dir/fs/"
cp "$susfs_dir/kernel_patches/include/linux/"* "$source_dir/include/linux/"
ln -s ../../ReSukiSU/kernel "$source_dir/drivers/kernelsu"
# shellcheck disable=SC2016 # Literal Kbuild variable, not shell substitution.
printf '\nobj-$(CONFIG_KSU) += kernelsu/\n' >> "$source_dir/drivers/Makefile"
python3 - "$source_dir/drivers/Kconfig" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
marker = '\nendmenu'
if marker not in s or 'source "drivers/kernelsu/Kconfig"' in s:
    raise SystemExit('unexpected pinned drivers/Kconfig layout')
p.write_text(s.replace(marker, '\nsource "drivers/kernelsu/Kconfig"' + marker, 1))
PY

out=$work/build
mkdir "$out"
python3 "$helper" configure "$work/stock/kernel.config" "$out/.config"
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git -C "$source_dir" show -s --format=%ct HEAD)
export KBUILD_BUILD_TIMESTAMP KBUILD_BUILD_USER=retcn-builder KBUILD_BUILD_HOST=retcn-susfs
KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$SOURCE_DATE_EPOCH" '+%a %b %d %H:%M:%S UTC %Y')
export KBUILD_BUILD_VERSION=1
make_args=(-C "$source_dir" O="$out" ARCH=arm64 LLVM=1 LLVM_IAS=1
  LOCALVERSION= BUILD_NUMBER= KCFLAGS=-D__ANDROID_COMMON_KERNEL__ KCONFIG_CONFIG="$out/.config"
  "USERCFLAGS=$USERCFLAGS" "USERLDFLAGS=$USERLDFLAGS")
make "${make_args[@]}" olddefconfig 2>&1 | tee "$work/configure.log"
# Preserve the complete drift before validation can stop the build. diff status 1
# means differences were found, not a command failure; other failures stay fatal.
diff -u --label stock-RETCN.config --label built-RETCN-SUSFS.config \
  "$work/stock/kernel.config" "$out/.config" > "$work/config.diff" || [[ $? == 1 ]]
python3 "$helper" validate-config "$work/stock/kernel.config" "$out/.config"
# Keep stock full LTO and CFI. Do not substitute ThinLTO merely to fit a runner.
make "${make_args[@]}" -j"$jobs" Image 2>&1 | tee "$work/build.log"
image=$out/arch/arm64/boot/Image
python3 "$project_root/scripts/extract-kernel-config.py" "$image" > "$work/embedded.config"
cmp "$out/.config" "$work/embedded.config"
python3 "$helper" validate-config "$work/stock/kernel.config" "$work/embedded.config"

mkdir -p "$(dirname -- "$artifact_dir")"
mkdir -- "$artifact_dir"
cp "$image" "$artifact_dir/Image"
cp "$out/vmlinux.symvers" "$artifact_dir/vmlinux.symvers"
cp "$work/compiler.txt" "$artifact_dir/compiler.txt"
cp "$work/userspace-toolchain.log" "$artifact_dir/userspace-toolchain.log"
cp "$work/configure.log" "$artifact_dir/configure.log"
cp "$project_root/config/rtwo-retcn/pins.env" "$artifact_dir/source-pins.env"
git -C "$source_dir" diff --binary > "$artifact_dir/source.patch"
# Newly copied SUSFS sources are pinned separately, not included in git diff.
cp "$susfs_dir/$SUSFS_PATCH" "$artifact_dir/upstream-susfs.patch"
cp "$work/build.log" "$artifact_dir/build.log"
{
  printf 'source_date_epoch=%s\nbuild_jobs=%s\n' "$SOURCE_DATE_EPOCH" "$jobs"
  printf 'pahole=%s\n' "$(pahole --version)"
  printf 'root_commit_count=%s\n' "$(git -C "$root_dir" rev-list --count HEAD)"
  printf 'validation=configuration-and-packaging-only; stock-module ABI/boot untested\n'
} > "$artifact_dir/build-metadata.txt"
python3 "$helper" package "$base_boot" "$image" "$artifact_dir"
(cd "$artifact_dir" && sha256sum Image kernel.config vmlinux.symvers > SHA256SUMS)
echo "RETCN artifacts: $artifact_dir; no device was queried or flashed"
