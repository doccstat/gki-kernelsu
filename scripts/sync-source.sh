#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/pins.env"

work_dir=${YOGI_WORKDIR:-$project_root/.work/gs101-android16-6.12}
source_dir=$work_dir/source
repo_bin=$work_dir/bin/repo
mkdir -p "$work_dir/bin"

if [[ ! -x "$repo_bin" ]]; then
  curl --fail --location --silent --show-error "$REPO_LAUNCHER_URL" > "$repo_bin"
  chmod 0755 "$repo_bin"
fi

if command -v shasum >/dev/null 2>&1; then
  launcher_sha=$(shasum -a 256 "$repo_bin" | awk '{print $1}')
else
  launcher_sha=$(sha256sum "$repo_bin" | awk '{print $1}')
fi
[[ "$launcher_sha" == "$REPO_LAUNCHER_SHA256" ]] || {
  echo "repo launcher checksum mismatch" >&2
  exit 1
}

if [[ ! -d "$source_dir/.repo" ]]; then
  mkdir -p "$source_dir"
  (cd "$source_dir" && "$repo_bin" init \
    --no-clone-bundle \
    --manifest-name default.xml \
    --manifest-url "$GOOGLE_MANIFEST_REPO" \
    --manifest-branch "$GOOGLE_MANIFEST_BRANCH")
fi

manifest_git=$source_dir/.repo/manifests
git -C "$manifest_git" fetch --no-tags origin "$GOOGLE_MANIFEST_REF"
git -C "$manifest_git" checkout --detach "$GOOGLE_MANIFEST_REF"

mkdir -p "$source_dir/.repo/local_manifests"
install -m 0644 "$project_root/config/manifest-lock.xml" \
  "$source_dir/.repo/local_manifests/yogi-lock.xml"

# The manifest checkout is deliberately detached at GOOGLE_MANIFEST_REF above.
# Without --no-manifest-update, repo sync tries to fetch the host Git default
# branch (master/main) for the manifests project and loses the pinned checkout.
(cd "$source_dir" && "$repo_bin" sync \
  --no-manifest-update \
  --current-branch \
  --force-sync \
  --no-clone-bundle \
  --no-tags \
  --jobs-network=4 \
  --jobs-checkout=4)

YOGI_WORKDIR="$work_dir" "$project_root/scripts/verify-source.sh"
echo "Google source is ready: $source_dir"
