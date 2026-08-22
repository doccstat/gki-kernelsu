#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work_dir=${YOGI_WORKDIR:-$project_root/.work/gs101-android16-6.12}
source_dir=$work_dir/source

[[ -d "$source_dir/.repo" ]] || { echo "source is not synced: $source_dir" >&2; exit 1; }

python3 - "$source_dir" "$project_root/config/manifest-lock.xml" <<'PY'
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

source = Path(sys.argv[1])
lock = ET.parse(sys.argv[2]).getroot()
errors = []
for project in lock.findall("extend-project"):
    project_path = project.attrib["path"]
    expected = project.attrib["revision"]
    checkout = source / project_path
    try:
        actual = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
    except subprocess.CalledProcessError:
        errors.append(f"missing checkout: {project_path}")
        continue
    if actual != expected:
        errors.append(f"{project_path}: expected {expected}, got {actual}")
if errors:
    raise SystemExit("\n".join(errors))
print(f"verified {len(lock.findall('extend-project'))} source checkouts")
PY

