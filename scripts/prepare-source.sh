#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/pins.env"

variant=${VARIANT:-sukisu-kpm}
variant_file="$project_root/config/variants/$variant.env"
[[ -f "$variant_file" ]] || { echo "unknown variant: $variant" >&2; exit 1; }
# shellcheck disable=SC1090
source "$variant_file"

if [[ "$ENABLE_KPM" == 1 && "$ENABLE_SUSFS" == 1 ]]; then
  echo "unsupported combined KPM+SUSFS variant" >&2
  exit 1
fi

work_dir=${YOGI_WORKDIR:-$project_root/.work/gs101-android16-6.12}
source_dir=$work_dir/source
common_dir=$source_dir/common
root_dir=$work_dir/root
mkdir -p "$root_dir"

clone_pinned() {
  local repo_url=$1
  local commit=$2
  local checkout_dir=$3
  if [[ ! -d "$checkout_dir/.git" ]]; then
    git clone --filter=blob:none --no-tags --no-checkout "$repo_url" "$checkout_dir"
  fi
  git -C "$checkout_dir" fetch --no-tags origin "$commit"
  git -C "$checkout_dir" checkout --detach "$commit"
}

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
updated = re.sub(r"^GITHUB_COMMITS\s*:=.*$", "GITHUB_COMMITS :=", updated, flags=re.MULTILINE)
if updated == text:
    raise SystemExit("SukiSU Kbuild offline pin did not match expected lines")
kbuild.write_text(updated)
PY
    python3 - "$root_source/kernel/kpm/super_access.c" <<'PY'
from pathlib import Path
import sys

source_path = Path(sys.argv[1])
text = source_path.read_text()
marker = "/* Android 16 / Linux 6.12 netlink_kernel_cfg compatibility. */"
if marker not in text:
    old = """DYNAMIC_STRUCT_BEGIN(netlink_kernel_cfg)
DEFINE_MEMBER(netlink_kernel_cfg, groups)
DEFINE_MEMBER(netlink_kernel_cfg, flags)
DEFINE_MEMBER(netlink_kernel_cfg, input)
DEFINE_MEMBER(netlink_kernel_cfg, cb_mutex)
DEFINE_MEMBER(netlink_kernel_cfg, bind)
DEFINE_MEMBER(netlink_kernel_cfg, unbind)
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 1, 0)
DEFINE_MEMBER(netlink_kernel_cfg, compare)
#endif
DYNAMIC_STRUCT_END(netlink_kernel_cfg)"""
    new = """/* Android 16 / Linux 6.12 netlink_kernel_cfg compatibility. */
DYNAMIC_STRUCT_BEGIN(netlink_kernel_cfg)
DEFINE_MEMBER(netlink_kernel_cfg, groups)
DEFINE_MEMBER(netlink_kernel_cfg, flags)
DEFINE_MEMBER(netlink_kernel_cfg, input)
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 12, 0)
DEFINE_MEMBER(netlink_kernel_cfg, cb_mutex)
#endif
DEFINE_MEMBER(netlink_kernel_cfg, bind)
DEFINE_MEMBER(netlink_kernel_cfg, unbind)
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 6, 0)
DEFINE_MEMBER(netlink_kernel_cfg, release)
#elif LINUX_VERSION_CODE < KERNEL_VERSION(6, 1, 0)
DEFINE_MEMBER(netlink_kernel_cfg, compare)
#endif
DYNAMIC_STRUCT_END(netlink_kernel_cfg)"""
    if old not in text:
        raise SystemExit(
            f"unexpected SukiSU netlink_kernel_cfg block in {source_path}"
        )
    source_path.write_text(text.replace(old, new, 1))
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

drivers_dir=$common_dir/drivers
kernel_link=$drivers_dir/kernelsu
expected_target=$(cd "$root_source/kernel" && pwd -P)
target_rel=$(python3 - "$drivers_dir" "$root_source/kernel" <<'PY'
import os
import sys
print(os.path.relpath(os.path.realpath(sys.argv[2]), os.path.realpath(sys.argv[1])))
PY
)
mkdir -p "$drivers_dir"
if [[ -L "$kernel_link" ]]; then
  actual_target=$(cd "$kernel_link" && pwd -P)
  if [[ "$actual_target" != "$expected_target" ]]; then
    rm "$kernel_link"
    ln -s "$target_rel" "$kernel_link"
  fi
elif [[ -e "$kernel_link" ]]; then
  echo "refusing to replace non-symlink $kernel_link" >&2
  exit 1
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

fragment=$source_dir/private/google-modules/soc/gs/arch/arm64/configs/slider_gki.fragment
[[ -f "$fragment" ]] || { echo "missing GS101 config fragment: $fragment" >&2; exit 1; }

# The mixed slider target inherits the GKI base kernel's post-defconfig KMI
# trimming fragment.  Kleaf applies that fragment after slider_gki.fragment and
# consequently reintroduces CONFIG_MODULE_SIG_PROTECT_LIST, which rejects the
# factory vendor_dlkm modules that are not signed with our custom key.  Disable
# KMI trimming on this device target; the stock vendor modules remain the ABI
# reference.
python3 - "$source_dir/private/google-modules/soc/gs/BUILD.bazel" <<'PY'
from pathlib import Path
import sys

build = Path(sys.argv[1])
text = build.read_text()
marker = "    # Yogi uses the stock vendor_dlkm modules; do not inherit GKI KMI trimming.\n    trim_nonlisted_kmi = False,\n"
if marker not in text:
    needle = "    module_outs = _SLIDER_MODULE_OUTS,\n"
    if text.count(needle) != 1:
        raise SystemExit(f"unexpected slider kernel_build() layout in {build}")
    text = text.replace(needle, marker + needle, 1)
    build.write_text(text)
PY

# slider_dist reuses the GKI base kernel's Image rather than linking a new
# device Image.  Configure that base target with the same KernelSU variant as
# the slider modules, while disabling GKI trimming/enforcement for this custom
# image.  Factory vendor_dlkm modules need the normal exports to remain
# available, and KPM/SUSFS add symbols outside Google's GKI KMI list.
python3 - "$source_dir/common/BUILD.bazel" <<'PY'
from pathlib import Path
import re
import sys

build = Path(sys.argv[1])
text = build.read_text()
pattern = re.compile(
    r'(common_kernel\(\n\s*name = "kernel_aarch64",.*?'
    r')\s*protected_module_names_list = ":gki_aarch64_protected_module_names",',
    re.DOTALL,
)
replacement = (
    r'\1'
    '\n'
    '    post_defconfig_fragments = ["arch/arm64/configs/yogi_root.fragment"],\n'
    '    # Yogi uses factory vendor_dlkm modules; do not protect exports.\n'
    '    protected_module_names_list = None,'
)
updated, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit(f"unexpected common kernel_aarch64 target layout in {build}")
updated, count = re.subn(
    r'(common_kernel\(\n\s*name = "kernel_aarch64",.*?'
    r'\n\s*kmi_enforced = )True,',
    r'\1False,',
    updated,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise SystemExit(f"cannot disable common kernel KMI enforcement in {build}")
updated, count = re.subn(
    r'(common_kernel\(\n\s*name = "kernel_aarch64",.*?'
    r'\n\s*kmi_symbol_list_strict_mode = )True,',
    r'\1False,',
    updated,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise SystemExit(f"cannot disable common kernel KMI strict mode in {build}")
updated, count = re.subn(
    r'(common_kernel\(\n\s*name = "kernel_aarch64",.*?'
    r'\n\s*trim_nonlisted_kmi = )True,',
    r'\1False,',
    updated,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise SystemExit(f"cannot disable common kernel KMI trimming in {build}")
build.write_text(updated)
PY

python3 - "$source_dir/build/kernel/kleaf/impl/defconfig/notrim_defconfig" <<'PY'
from pathlib import Path
import sys

fragment = Path(sys.argv[1])
text = fragment.read_text()
expected = "# CONFIG_MODULE_SIG_PROTECT is not set\n"
replacement = expected + 'CONFIG_MODULE_SIG_PROTECT_LIST=""\n'
if text == expected:
    fragment.write_text(replacement)
elif text != replacement:
    raise SystemExit(f"unexpected Kleaf no-trim fragment in {fragment}")
PY

root_fragment=$common_dir/arch/arm64/configs/yogi_root.fragment
: > "$root_fragment"
config_fragments=("$fragment" "$root_fragment")

set_config() {
  local key=$1 value=$2
  for config_fragment in "${config_fragments[@]}"; do
    sed -i -E "/^(# )?CONFIG_${key}(=.*| is not set)$/d" "$config_fragment"
    printf 'CONFIG_%s=%s\n' "$key" "$value" >> "$config_fragment"
  done
}

unset_config() {
  local key=$1
  for config_fragment in "${config_fragments[@]}"; do
    sed -i -E "/^(# )?CONFIG_${key}(=.*| is not set)$/d" "$config_fragment"
    printf '# CONFIG_%s is not set\n' "$key" >> "$config_fragment"
  done
}

set_config KSU y
set_config KPROBES y
set_config KSU_DEBUG n
set_config KSU_DISABLE_MANAGER n
set_config KSU_DISABLE_POLICY n

# Yogi keeps Google's stock vendor_dlkm modules.  The custom kernel is signed
# with a locally generated key, so those stock modules cannot satisfy the
# protected-export policy.  Leave normal module signature verification
# enabled, but disable export protection for this mixed custom-kernel/stock-
# module arrangement; otherwise rfkill.ko is rejected before Wi-Fi loads.
set_config MODULE_SIG_PROTECT_LIST '""'
unset_config MODULE_SIG_PROTECT

if [[ "$ENABLE_KPM" == 1 ]]; then
  set_config KPM y
  unset_config KSU_SUSFS
else
  unset_config KPM
fi

if [[ "$ENABLE_SUSFS" == 1 ]]; then
  susfs_dir=$root_dir/susfs4ksu
  clone_pinned "$SUSFS_REPO" "$SUSFS_REF" "$susfs_dir"
  marker="$common_dir/.yogi-susfs-$SUSFS_REF"
  if [[ ! -e "$marker" ]]; then
    cp "$susfs_dir/kernel_patches/fs/"* "$common_dir/fs/"
    cp "$susfs_dir/kernel_patches/include/linux/"* "$common_dir/include/linux/"

    # The pinned SUSFS patch is correct for Android 16/6.12, but two Google
    # downstream changes move/rename the exact context of its hunks: exec.c
    # inserts dma-buf between ksm and uaccess, while task_mmu.c uses
    # vma_data_pages() in show_smap(). Normalize those contexts only while the
    # upstream patch is applied, then restore Google's source spelling.
    python3 - "$common_dir/fs/exec.c" "$common_dir/fs/proc/task_mmu.c" <<'PY'
from pathlib import Path
import sys

exec_path = Path(sys.argv[1])
exec_text = exec_path.read_text()
dma_include = "#include <linux/dma-buf.h>\n"
if exec_text.count(dma_include) != 1:
    raise SystemExit(f"unexpected dma-buf include layout in {exec_path}")
exec_path.write_text(exec_text.replace(dma_include, "", 1))

task_path = Path(sys.argv[2])
task_text = task_path.read_text()
downstream = "\tif (!vma_data_pages(vma))\n"
if task_text.count(downstream) != 1:
    raise SystemExit(f"unexpected show_smap layout in {task_path}")
task_path.write_text(task_text.replace(downstream, "\tif (!vma_pages(vma))\n", 1))
PY

    susfs_patch_status=0
    patch --batch --forward --silent -d "$common_dir" -p1 < \
      "$susfs_dir/$SUSFS_PATCH" || susfs_patch_status=$?

    python3 - "$common_dir/fs/exec.c" "$common_dir/fs/proc/task_mmu.c" <<'PY'
from pathlib import Path
import sys

exec_path = Path(sys.argv[1])
exec_text = exec_path.read_text()
dma_include = "#include <linux/dma-buf.h>\n"
if dma_include not in exec_text:
    marker = "#include <linux/uaccess.h>\n"
    if exec_text.count(marker) != 1:
        raise SystemExit(f"cannot restore dma-buf include in {exec_path}")
    exec_text = exec_text.replace(marker, dma_include + "\n" + marker, 1)
exec_path.write_text(exec_text)

task_path = Path(sys.argv[2])
task_text = task_path.read_text()
normalized = "\tif (!vma_pages(vma))\n"
downstream = "\tif (!vma_data_pages(vma))\n"
if task_text.count(normalized) != 1:
    raise SystemExit(f"cannot restore show_smap layout in {task_path}")
task_path.write_text(task_text.replace(normalized, downstream, 1))
PY

    (( susfs_patch_status == 0 )) || exit "$susfs_patch_status"
    touch "$marker"
  fi
  set_config KSU_SUSFS y
  set_config KSU_SUSFS_SUS_PATH y
  set_config KSU_SUSFS_SUS_MOUNT y
  set_config KSU_SUSFS_SUS_KSTAT y
  set_config KSU_SUSFS_SPOOF_UNAME y
  set_config KSU_SUSFS_ENABLE_LOG y
  set_config KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS y
  set_config KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG y
  set_config KSU_SUSFS_OPEN_REDIRECT y
  set_config KSU_SUSFS_SUS_MAP y
  unset_config KSU_TRACEPOINT_HOOK
  unset_config KSU_MANUAL_HOOK
else
  unset_config KSU_SUSFS
  unset_config KSU_TRACEPOINT_HOOK
  unset_config KSU_MANUAL_HOOK
fi

printf '%s\n' "$VARIANT_NAME" > "$work_dir/selected-variant"
{
  echo "[common]"
  git -C "$common_dir" status --short
  echo "[root]"
  git -C "$root_source" status --short
} > "$work_dir/source-modifications.txt"
echo "prepared $VARIANT_NAME in $source_dir"
