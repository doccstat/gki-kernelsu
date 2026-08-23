#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/rtwo/pins.env"

tools_dir=${MKBOOTIMG_DIR:-$project_root/.work/mkbootimg}
mkdir -p "$(dirname "$tools_dir")"

if [[ ! -d "$tools_dir/.git" ]]; then
  git clone --filter=blob:none --no-tags --no-checkout \
    "$MKBOOTIMG_REPO" "$tools_dir"
fi
git -C "$tools_dir" fetch --no-tags origin "$MKBOOTIMG_REF"
git -C "$tools_dir" reset --hard "$MKBOOTIMG_REF" >/dev/null
git -C "$tools_dir" clean -fdx >/dev/null
git -C "$tools_dir" checkout --detach "$MKBOOTIMG_REF" >/dev/null

[[ -f "$tools_dir/mkbootimg.py" ]] || {
  echo "pinned mkbootimg checkout has no mkbootimg.py" >&2
  exit 1
}
[[ -f "$tools_dir/unpack_bootimg.py" ]] || {
  echo "pinned mkbootimg checkout has no unpack_bootimg.py" >&2
  exit 1
}
echo "using mkbootimg tools at $MKBOOTIMG_REF"
