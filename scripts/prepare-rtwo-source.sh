#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/rtwo/pins.env"

if [[ "$(uname -s)" != Linux ]]; then
  cat >&2 <<'EOF'
rtwo source preparation must run on a case-sensitive Linux filesystem.
The upstream Qualcomm tree contains case-colliding paths (for example
README.md/Readme.md and uppercase/lowercase netfilter headers), so macOS
cannot safely check it out. Use the rtwo GitHub Actions workflow.
EOF
  exit 2
fi

variant=${VARIANT:-sukisu-kpm}
variant_file="$project_root/config/rtwo/variants/$variant.env"
[[ -f "$variant_file" ]] || { echo "unknown rtwo variant: $variant" >&2; exit 1; }
# shellcheck disable=SC1090
source "$variant_file"

if [[ "$ENABLE_KPM" == 1 && "$ENABLE_SUSFS" == 1 ]]; then
  echo "unsupported combined KPM+SUSFS variant" >&2
  exit 1
fi

work_dir=${RTWO_WORKDIR:-$project_root/.work/rtwo-android13-5.15/$variant}
source_dir=$work_dir/source
root_dir=$work_dir/root
mkdir -p "$work_dir" "$root_dir"

clone_pinned() {
  local repo_url=$1 commit=$2 checkout_dir=$3
  if [[ ! -d "$checkout_dir/.git" ]]; then
    git clone --filter=blob:none --no-tags --no-checkout "$repo_url" "$checkout_dir"
  fi
  git -C "$checkout_dir" fetch --no-tags origin "$commit"
  git -C "$checkout_dir" reset --hard "$commit" >/dev/null
  git -C "$checkout_dir" clean -fdx >/dev/null
  git -C "$checkout_dir" checkout --detach "$commit" >/dev/null
}

clone_pinned "$KERNEL_REPO" "$KERNEL_REF" "$source_dir"
clone_pinned "$MODULES_REPO" "$MODULES_REF" "$source_dir/../sm8550-modules"
clone_pinned "$DEVICETREES_REPO" "$DEVICETREES_REF" "$source_dir/../sm8550-devicetrees"

case "$ROOT_KIND" in
  sukisu)
    clone_pinned "$SUKISU_REPO" "$SUKISU_REF" "$root_dir/SukiSU-Ultra"
    root_source=$root_dir/SukiSU-Ultra
    python3 - "$root_source/kernel/Kbuild" <<'PY'
import re
from pathlib import Path
import sys

kbuild = Path(sys.argv[1])
text = kbuild.read_text()
updated = re.sub(r"^KSU_GITHUB_VER\s*:=.*$", "KSU_GITHUB_VER := 4.1.3", text, flags=re.MULTILINE)
updated = re.sub(r"^GITHUB_COMMITS\s*:=.*$", "GITHUB_COMMITS   :=", updated, flags=re.MULTILINE)
if updated == text:
    raise SystemExit("SukiSU Kbuild offline pin did not match expected lines")
kbuild.write_text(updated)
PY
    ;;
  resukisu)
    clone_pinned "$RESUKISU_REPO" "$RESUKISU_REF" "$root_dir/ReSukiSU"
    root_source=$root_dir/ReSukiSU
    ;;
  *)
    echo "invalid ROOT_KIND=$ROOT_KIND" >&2
    exit 1
    ;;
esac

drivers_dir=$source_dir/drivers
kernel_link=$drivers_dir/kernelsu
expected_target=$(cd "$root_source/kernel" && pwd -P)
target_rel=$(python3 - "$drivers_dir" "$root_source/kernel" <<'PY'
import os
import sys
print(os.path.relpath(os.path.realpath(sys.argv[2]), os.path.realpath(sys.argv[1])))
PY
)
mkdir -p "$drivers_dir"
if [[ -e "$kernel_link" && ! -L "$kernel_link" ]]; then
  echo "refusing to replace non-symlink $kernel_link" >&2
  exit 1
fi
if [[ -L "$kernel_link" ]]; then
  actual_target=$(cd "$kernel_link" && pwd -P)
  [[ "$actual_target" == "$expected_target" ]] || { rm "$kernel_link"; ln -s "$target_rel" "$kernel_link"; }
else
  ln -s "$target_rel" "$kernel_link"
fi

grep -qxF 'obj-$(CONFIG_KSU) += kernelsu/' "$drivers_dir/Makefile" 2>/dev/null ||
  printf '\nobj-$(CONFIG_KSU) += kernelsu/\n' >> "$drivers_dir/Makefile"

python3 - "$drivers_dir/Kconfig" <<'PY'
from pathlib import Path
import sys

kconfig = Path(sys.argv[1])
text = kconfig.read_text()
entry = 'source "drivers/kernelsu/Kconfig"'
if entry not in text:
    marker = "\nendmenu"
    if marker not in text:
        raise SystemExit(f"cannot find endmenu in {kconfig}")
    kconfig.write_text(text.replace(marker, f"\n{entry}{marker}", 1))
PY

if [[ "$ENABLE_SUSFS" == 1 ]]; then
  susfs_dir=$root_dir/susfs4ksu
  clone_pinned "$SUSFS_REPO" "$SUSFS_REF" "$susfs_dir"
  cp "$susfs_dir/kernel_patches/fs/"* "$source_dir/fs/"
  cp "$susfs_dir/kernel_patches/include/linux/"* "$source_dir/include/linux/"
  # The current 5.15.208 source carries these trace-hook includes while the
  # pinned SUSFS patch was authored against an older 5.15 layout. Remove them
  # temporarily so the patch context applies, then restore them below because
  # the Qualcomm source still calls the hooks they declare.
  python3 - "$source_dir/fs/namespace.c" "$source_dir/fs/proc/task_mmu.c" <<'PY'
from pathlib import Path
import sys

for name, needle in (
    (sys.argv[1], "#include <trace/hooks/blk.h>"),
    (sys.argv[2], "#include <trace/hooks/mm.h>"),
):
    path = Path(name)
    path.write_text("".join(line for line in path.read_text().splitlines(True)
                            if line.strip() != needle))
PY
  patch --batch --forward --silent -d "$source_dir" -p1 < "$susfs_dir/$SUSFS_PATCH"
  python3 - "$source_dir/fs/namespace.c" "$source_dir/fs/proc/task_mmu.c" <<'PY'
from pathlib import Path
import sys

for name, anchor, include in (
    (sys.argv[1], '#include "internal.h"', '#include <trace/hooks/blk.h>'),
    (sys.argv[2], '#include <linux/pkeys.h>', '#include <trace/hooks/mm.h>'),
):
    path = Path(name)
    lines = path.read_text().splitlines(True)
    if any(line.strip() == include for line in lines):
        continue
    for index, line in enumerate(lines):
        if line.strip() == anchor:
            lines.insert(index + 1, include + '\n')
            break
    else:
        raise SystemExit(f'cannot restore {include} in {path}')
    path.write_text(''.join(lines))
PY
fi

cat > "$work_dir/root.fragment" <<EOF
CONFIG_KSU=y
CONFIG_KPROBES=y
# CONFIG_KSU_DEBUG is not set
# CONFIG_KSU_DISABLE_MANAGER is not set
# CONFIG_KSU_DISABLE_POLICY is not set
EOF

if [[ "$ENABLE_KPM" == 1 ]]; then
  printf 'CONFIG_KPM=y\n# CONFIG_KSU_SUSFS is not set\n' >> "$work_dir/root.fragment"
else
  cat >> "$work_dir/root.fragment" <<'EOF'
# CONFIG_KPM is not set
CONFIG_KSU_SUSFS=y
CONFIG_KSU_SUSFS_SUS_PATH=y
CONFIG_KSU_SUSFS_SUS_MOUNT=y
CONFIG_KSU_SUSFS_SUS_KSTAT=y
CONFIG_KSU_SUSFS_SPOOF_UNAME=y
CONFIG_KSU_SUSFS_ENABLE_LOG=y
CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y
CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y
CONFIG_KSU_SUSFS_OPEN_REDIRECT=y
CONFIG_KSU_SUSFS_SUS_MAP=y
# CONFIG_KSU_TRACEPOINT_HOOK is not set
# CONFIG_KSU_MANUAL_HOOK is not set
EOF
fi

printf '%s\n' "$VARIANT_NAME" > "$work_dir/selected-variant"
{
  echo "kernel=$(git -C "$source_dir" rev-parse HEAD)"
  echo "modules=$(git -C "$source_dir/../sm8550-modules" rev-parse HEAD)"
  echo "devicetrees=$(git -C "$source_dir/../sm8550-devicetrees" rev-parse HEAD)"
  echo "root=$(git -C "$root_source" rev-parse HEAD)"
  [[ "$ENABLE_SUSFS" == 1 ]] && echo "susfs=$(git -C "$root_dir/susfs4ksu" rev-parse HEAD)"
} > "$work_dir/source-pins.txt"
echo "prepared rtwo $VARIANT_NAME in $source_dir"
