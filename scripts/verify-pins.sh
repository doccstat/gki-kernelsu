#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$project_root/config/pins.env"

is_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

for name in GOOGLE_MANIFEST_REF GOOGLE_SUPERPROJECT_REF COMMON_KERNEL_REF \
  SUKISU_REF RESUKISU_REF SUSFS_REF REPO_LAUNCHER_SHA256; do
  value=${!name}
  if [[ "$name" == REPO_LAUNCHER_SHA256 ]]; then
    [[ "$value" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid $name" >&2; exit 1; }
  else
    is_sha "$value" || { echo "invalid $name=$value" >&2; exit 1; }
  fi
done

python3 - "$project_root/config/manifest-lock.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

lock = ET.parse(sys.argv[1]).getroot()
projects = lock.findall("extend-project")
if not projects:
    raise SystemExit("manifest lock has no projects")
seen = set()
for project in projects:
    project_path = project.attrib.get("path")
    revision = project.attrib.get("revision", "")
    if not project_path or project_path in seen:
        raise SystemExit(f"duplicate or missing project path: {project_path!r}")
    if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
        raise SystemExit(f"non-immutable revision for {project_path}: {revision}")
    seen.add(project_path)
print(f"validated {len(projects)} immutable Google project pins")
PY

for variant_file in "$project_root"/config/variants/*.env; do
  # shellcheck disable=SC1090
  source "$variant_file"
  [[ "$ENABLE_KPM" == 0 || "$ENABLE_KPM" == 1 ]] || exit 1
  [[ "$ENABLE_SUSFS" == 0 || "$ENABLE_SUSFS" == 1 ]] || exit 1
  if [[ "$ENABLE_KPM" == 1 && "$ENABLE_SUSFS" == 1 ]]; then
    echo "refusing unsupported combined KPM+SUSFS variant: $VARIANT_NAME" >&2
    exit 1
  fi
  echo "validated variant $VARIANT_NAME"
done

